package com.filmlab.mobile;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;

/** Pure-Java port of the developed-sRGB path in film_lab, version 1.0.0.
 * Input pixels must already be oriented, opaque, encoded sRGB. Resampling,
 * camera RAW, ICC conversion, and decoding/encoding are the caller's concern.
 * Profiles are immutable and cached; concurrent renders are safe. Working
 * memory is one output int[] and a few small arrays, independent of pixel count.
 */
public final class FilmEngine {
    public interface ProfileSource { InputStream open(String relativePath) throws IOException; }

    public static final class Settings {
        public String styleId = "baseline";
        public float exposure = 0, strength = 1;
        public int warmth, tint, contrast, saturation, highlights, shadows, grain;
        /** Use the original file's SHA-256 to reproduce desktop grain exactly. */
        public String sourceHash = "android";
        public int grainSeed = 12345;
        public boolean largeGrain;

        public Settings copy() {
            Settings s = new Settings();
            s.styleId = styleId; s.exposure = exposure; s.strength = strength;
            s.warmth = warmth; s.tint = tint; s.contrast = contrast;
            s.saturation = saturation; s.highlights = highlights; s.shadows = shadows;
            s.grain = grain; s.sourceHash = sourceHash; s.grainSeed = grainSeed;
            s.largeGrain = largeGrain;
            return s;
        }
    }

    private static final String[] IDS = {
        "baseline", "documentary", "negative", "cinema", "vivid", "monochrome"
    };
    // Bind this port to the same versioned assets as the desktop engine.
    private static final String[] HASHES = {
        "881236ec2c64ab53e0b9636344d1b967e0d98b28f715053b87c89aea9a588a3d",
        "880dcd40b001a6c631a6e78e732533e5f5a93f8be88036be2e94d5f76fad19fe",
        "54f075fc36dd63a86e944cc0bd00ae6c38fcdb5c087f0f5401c3af0b9219e7eb",
        "b578ac751ee71e443367ec8969cdadae691ee5d8d40b0f6e55291ff6a2911cb0",
        "1af7460ffd6c3ff4cf8e2a393361ec8eb3db9ce9d06e89c49cbce903ac615970",
        "4013a0e4ce6c73384afce44d30c6bcce8b8bb865fd4e1ed16833b380a8f5d45c"
    };
    private static final float[] MONO_X = {0, .06f, .16f, .35f, .55f, .78f, .92f, 1};
    private static final float[] MONO_Y = {0, .025f, .1f, .3f, .56f, .85f, .96f, 1};
    private final ProfileSource source;
    private final Map<String, Cube> profiles = new HashMap<>();

    public FilmEngine(ProfileSource source) {
        if (source == null) throw new IllegalArgumentException("Profile source is required");
        this.source = source;
    }

    public FilmEngine(File profilesRoot) {
        this(relativePath -> new FileInputStream(new File(profilesRoot, relativePath)));
    }

    public int[] render(int[] argb, int width, int height, Settings settings,
                        int originalWidth, int originalHeight) {
        return render(argb, width, height, settings, originalWidth, originalHeight, () -> false);
    }

    public int[] render(int[] argb, int width, int height, Settings settings,
                        int originalWidth, int originalHeight, BooleanSupplier cancelled) {
        if (argb == null || width <= 0 || height <= 0 || (long) width * height != argb.length)
            throw new IllegalArgumentException("Invalid pixel dimensions");
        if (originalWidth <= 0 || originalHeight <= 0 || settings == null || cancelled == null)
            throw new IllegalArgumentException("Invalid render settings");
        Settings s = settings.copy();
        validate(s);
        checkCancelled(cancelled);
        Cube cube = profile(s.styleId);
        boolean mono = "monochrome".equals(s.styleId);
        boolean applyStyle = !"baseline".equals(s.styleId) && s.strength != 0;
        float inverseStrength = 1 - s.strength;
        float power = (float) Math.pow(2, s.contrast / 100.0);
        float saturation = mono ? 0 : s.saturation / 100f;
        float highlights = s.highlights / 100f, shadows = s.shadows / 100f;
        boolean prepare = s.exposure != 0 || s.warmth != 0 || s.tint != 0;
        float exp = (float) Math.pow(2, s.exposure);
        float redGain = (float) Math.pow(2, s.warmth / 100.0 * .45) * exp;
        float greenGain = (float) Math.pow(2, -s.tint / 100.0 * .25) * exp;
        float blueGain = (float) Math.pow(2, -s.warmth / 100.0 * .45) * exp;
        float[] reds = new float[256], greens = new float[256], blues = new float[256];
        for (int i = 0; i < 256; i++) {
            float v = i / 255f;
            if (prepare) {
                float linear = decode(v);
                reds[i] = encode(linear * redGain);
                greens[i] = encode(linear * greenGain);
                blues[i] = encode(linear * blueGain);
            } else reds[i] = greens[i] = blues[i] = v;
        }
        double sx = originalWidth / (double) width, sy = originalHeight / (double) height;
        double cell = s.largeGrain ? 3.0 : 1.25;
        float amplitude = (float) (s.grain / 100.0 * .055 / Math.sqrt(Math.max(1, sx * sy / (cell * cell))));
        int seed = s.grain == 0 ? 0 : grainSeed(s.sourceHash, s.grainSeed);
        float[] styled = new float[3];
        int[] output = new int[argb.length];
        for (int row = 0; row < height; row++) {
            checkCancelled(cancelled);
            double gy = (row * sy + sy / 2) / cell;
            int yi = (int) Math.floor(gy);
            float yf = (float) (gy - Math.floor(gy));
            int end = (row + 1) * width;
            for (int at = row * width; at < end; at++) {
                int pixel = argb[at];
                float r = reds[(pixel >>> 16) & 255];
                float g = greens[(pixel >>> 8) & 255];
                float b = blues[pixel & 255];
                if (applyStyle) {
                    if (mono) {
                        float tone = monoCurve(encode(decode(r) * .25f + decode(g) * .65f + decode(b) * .1f));
                        styled[0] = styled[1] = styled[2] = tone;
                    } else cube.apply(r, g, b, styled);
                    if (s.strength == 1) { r = styled[0]; g = styled[1]; b = styled[2]; }
                    else {
                        r = styled[0] * s.strength + r * inverseStrength;
                        g = styled[1] * s.strength + g * inverseStrength;
                        b = styled[2] * s.strength + b * inverseStrength;
                    }
                }
                if (s.contrast != 0) { r = contrast(r, power); g = contrast(g, power); b = contrast(b, power); }
                if (highlights != 0 || shadows != 0) {
                    float y = luma(r, g, b), inverseY = 1 - y;
                    float delta = (highlights * .8f * y * y + shadows * .8f * inverseY * inverseY) * y * inverseY;
                    r = clamp(r + delta); g = clamp(g + delta); b = clamp(b + delta);
                }
                if (saturation != 0) {
                    r = decode(r); g = decode(g); b = decode(b);
                    float y = luma(r, g, b);
                    r = y + (r - y) * (1 + saturation);
                    g = y + (g - y) * (1 + saturation);
                    b = y + (b - y) * (1 + saturation);
                    y = clamp(luma(r, g, b));
                    float dr = r - y, dg = g - y, db = b - y;
                    float up = Math.max(1 - y, 1e-7f), down = Math.max(y, 1e-7f);
                    float excursion = Math.max(Math.max(dr >= 0 ? dr / up : -dr / down,
                        dg >= 0 ? dg / up : -dg / down), db >= 0 ? db / up : -db / down);
                    float factor = 1;
                    if (excursion > .8f) {
                        float soft = .8f + .2f * (float) -Math.expm1(-(excursion - .8f) / .2f);
                        factor = soft / Math.max(excursion, 1e-7f);
                    }
                    r = encode(y + dr * factor); g = encode(y + dg * factor); b = encode(y + db * factor);
                }
                if (s.grain != 0) {
                    int column = at - row * width;
                    double gx = (column * sx + sx / 2) / cell;
                    int xi = (int) Math.floor(gx);
                    float xf = (float) (gx - Math.floor(gx));
                    float n00 = coordinateNoise(xi, yi, seed), n10 = coordinateNoise(xi + 1, yi, seed);
                    float n01 = coordinateNoise(xi, yi + 1, seed), n11 = coordinateNoise(xi + 1, yi + 1, seed);
                    float noise = (n00 * (1 - xf) + n10 * xf) * (1 - yf) + (n01 * (1 - xf) + n11 * xf) * yf;
                    float y = clamp(luma(r, g, b));
                    float envelope = .35f + .65f * (float) Math.sqrt(4 * y * (1 - y));
                    if (mono) envelope *= 1.15f - .3f * y;
                    float offset = noise * amplitude * envelope;
                    r = clamp(r + offset); g = clamp(g + offset); b = clamp(b + offset);
                }
                output[at] = 0xff000000 | (quantize(r) << 16) | (quantize(g) << 8) | quantize(b);
            }
        }
        checkCancelled(cancelled);
        return output;
    }

    private static void validate(Settings s) {
        if (s.styleId == null || s.sourceHash == null || !Float.isFinite(s.exposure)
                || Math.abs(s.exposure) > 5 || !Float.isFinite(s.strength) || s.strength < 0 || s.strength > 1)
            throw new IllegalArgumentException("Invalid exposure, style, or strength");
        int[] signed = {s.warmth, s.tint, s.contrast, s.saturation, s.highlights, s.shadows};
        for (int value : signed) if (value < -100 || value > 100) throw new IllegalArgumentException("Adjustment outside -100..100");
        if (s.grain < 0 || s.grain > 100) throw new IllegalArgumentException("Grain outside 0..100");
    }

    private synchronized Cube profile(String id) {
        if (profiles.containsKey(id)) return profiles.get(id);
        int styleIndex = -1;
        for (int i = 0; i < IDS.length; i++) if (IDS[i].equals(id)) styleIndex = i;
        if (styleIndex < 0) throw new IllegalArgumentException("Unknown profile: " + id);
        boolean isCube = styleIndex > 0 && styleIndex < 5;
        MessageDigest digest = sha256();
        try (DigestInputStream stream = new DigestInputStream(source.open(id + (isCube ? "/style.cube" : "/curve.json")), digest)) {
            Cube cube;
            if (isCube) cube = readCube(stream);
            else {
                byte[] scratch = new byte[4096];
                while (stream.read(scratch) != -1) { /* Validate compiled curve against original asset. */ }
                cube = null;
            }
            if (!hex(digest.digest()).equals(HASHES[styleIndex])) throw new IOException("Profile checksum mismatch: " + id);
            profiles.put(id, cube);
            return cube;
        } catch (IOException | NumberFormatException e) {
            throw new IllegalStateException("Could not load film profile: " + id, e);
        }
    }

    private static Cube readCube(InputStream stream) throws IOException {
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
        float[] table = null;
        int size = 0, count = 0;
        String line;
        while ((line = reader.readLine()) != null) {
            int comment = line.indexOf('#');
            line = (comment >= 0 ? line.substring(0, comment) : line).trim();
            if (line.isEmpty()) continue;
            if (line.startsWith("TITLE ") || line.startsWith("DOMAIN_MIN ") || line.startsWith("DOMAIN_MAX ")) continue;
            String[] fields = line.split("\\s+");
            if (fields[0].equals("LUT_3D_SIZE")) {
                if (table != null || fields.length != 2) throw new IOException("Invalid cube size header");
                size = Integer.parseInt(fields[1]);
                if (size != 33) throw new IOException("Expected version 1.0.0, 33-point LUT");
                table = new float[size * size * size * 3];
            } else {
                if (table == null || fields.length != 3 || count + 3 > table.length) throw new IOException("Invalid cube sample");
                for (String field : fields) {
                    float value = Float.parseFloat(field);
                    if (!Float.isFinite(value) || value < 0 || value > 1) throw new IOException("Invalid cube value");
                    table[count++] = value;
                }
            }
        }
        if (table == null || count != table.length) throw new IOException("Incomplete cube");
        return new Cube(table, size);
    }

    private static final class Cube {
        private final float[] table;
        private final int size;
        Cube(float[] table, int size) { this.table = table; this.size = size; }

        void apply(float r, float g, float b, float[] output) {
            r = clamp(r) * (size - 1); g = clamp(g) * (size - 1); b = clamp(b) * (size - 1);
            int ri = Math.min((int) r, size - 2), gi = Math.min((int) g, size - 2), bi = Math.min((int) b, size - 2);
            float fr = r - ri, fg = g - gi, fb = b - bi;
            int sr = 3, sg = size * 3, sb = size * size * 3;
            int base = (bi * size * size + gi * size + ri) * 3;
            int first, second;
            float f1, f2, f3;
            // Stable descending R/G/B order also covers all shared faces.
            if (fr >= fg) {
                if (fg >= fb) { first = sr; second = sr + sg; f1 = fr; f2 = fg; f3 = fb; }
                else if (fr >= fb) { first = sr; second = sr + sb; f1 = fr; f2 = fb; f3 = fg; }
                else { first = sb; second = sb + sr; f1 = fb; f2 = fr; f3 = fg; }
            } else {
                if (fr >= fb) { first = sg; second = sg + sr; f1 = fg; f2 = fr; f3 = fb; }
                else if (fg >= fb) { first = sg; second = sg + sb; f1 = fg; f2 = fb; f3 = fr; }
                else { first = sb; second = sb + sg; f1 = fb; f2 = fg; f3 = fr; }
            }
            for (int channel = 0; channel < 3; channel++) {
                int i = base + channel;
                float c0 = table[i], c1 = table[i + first], c2 = table[i + second], c3 = table[i + sr + sg + sb];
                output[channel] = c0 + f1 * (c1 - c0) + f2 * (c2 - c1) + f3 * (c3 - c2);
            }
        }
    }

    private static float decode(float v) {
        v = clamp(v);
        return v <= .04045f ? v / 12.92f : (float) Math.pow((v + .055f) / 1.055f, 2.4f);
    }
    private static float encode(float v) {
        v = clamp(v);
        return v <= .0031308f ? v * 12.92f : 1.055f * (float) Math.pow(v, 1f / 2.4f) - .055f;
    }
    private static float contrast(float v, float power) {
        float numerator = (float) Math.pow(clamp(v), power);
        return numerator / Math.max(numerator + (float) Math.pow(1 - clamp(v), power), 1e-8f);
    }
    private static float monoCurve(float v) {
        for (int i = 1; i < MONO_X.length; i++) {
            if (v <= MONO_X[i]) return (float) (MONO_Y[i - 1] + ((double) v - MONO_X[i - 1])
                * ((double) MONO_Y[i] - MONO_Y[i - 1]) / ((double) MONO_X[i] - MONO_X[i - 1]));
        }
        return 1;
    }
    private static float luma(float r, float g, float b) { return r * .2126f + g * .7152f + b * .0722f; }
    private static float clamp(float v) { return Math.max(0, Math.min(1, v)); }
    private static int quantize(float v) { return Math.min(255, Math.max(0, (int) (v * 255f + .5f))); }
    private static void checkCancelled(BooleanSupplier cancelled) {
        if (Thread.currentThread().isInterrupted() || cancelled.getAsBoolean()) throw new CancellationException("Render cancelled");
    }
    private static float coordinateNoise(int x, int y, int seed) {
        int value = (x * 0x1F123BB5) ^ (y * 0x5F356495) ^ seed;
        value ^= value >>> 16; value *= 0x7FEB352D;
        value ^= value >>> 15; value *= 0x846CA68B;
        value ^= value >>> 16;
        return (value >>> 8) / 8388607.5f - 1;
    }
    private static int grainSeed(String sourceHash, int seed) {
        byte[] bytes = sha256().digest((sourceHash + ":" + seed).getBytes(StandardCharsets.UTF_8));
        return (bytes[0] & 255) | ((bytes[1] & 255) << 8) | ((bytes[2] & 255) << 16) | ((bytes[3] & 255) << 24);
    }
    private static MessageDigest sha256() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException e) { throw new AssertionError(e); }
    }
    private static String hex(byte[] bytes) {
        char[] digits = "0123456789abcdef".toCharArray(), out = new char[bytes.length * 2];
        for (int i = 0; i < bytes.length; i++) { out[i * 2] = digits[(bytes[i] >>> 4) & 15]; out[i * 2 + 1] = digits[bytes[i] & 15]; }
        return new String(out);
    }
}
