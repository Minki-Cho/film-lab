import com.filmlab.mobile.FilmEngine;
import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.concurrent.CancellationException;

/** Runs on a normal JDK: no Android SDK, emulator, or test framework required. */
public final class FilmEngineTest {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("Usage: FilmEngineTest profilesDirectory references.tsv");
        FilmEngine engine = new FilmEngine(new File(args[0]));
        int cases = 0, compared = 0, maxDelta = 0, differing = 0;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(new FileInputStream(args[1]), StandardCharsets.UTF_8))) {
            reader.readLine();
            String line;
            while ((line = reader.readLine()) != null) {
                String[] fields = line.split("\t");
                FilmEngine.Settings s = new FilmEngine.Settings();
                s.styleId = fields[1]; s.exposure = Float.parseFloat(fields[2]); s.strength = Float.parseFloat(fields[3]);
                s.warmth = Integer.parseInt(fields[4]); s.tint = Integer.parseInt(fields[5]);
                s.contrast = Integer.parseInt(fields[6]); s.saturation = Integer.parseInt(fields[7]);
                s.highlights = Integer.parseInt(fields[8]); s.shadows = Integer.parseInt(fields[9]);
                s.grain = Integer.parseInt(fields[10]); s.largeGrain = Boolean.parseBoolean(fields[15]); s.sourceHash = fields[16];
                int w = Integer.parseInt(fields[11]), h = Integer.parseInt(fields[12]);
                int ow = Integer.parseInt(fields[13]), oh = Integer.parseInt(fields[14]);
                int[] input = pixels(fields[17]), expected = pixels(fields[18]), saved = input.clone();
                int[] actual = engine.render(input, w, h, s, ow, oh);
                if (!Arrays.equals(input, saved)) throw new AssertionError("Input mutated: " + fields[0]);
                for (int p = 0; p < actual.length; p++) {
                    if ((actual[p] >>> 24) != 255) throw new AssertionError("Nonopaque output");
                    for (int shift = 0; shift <= 16; shift += 8) {
                        int delta = Math.abs(((actual[p] >>> shift) & 255) - ((expected[p] >>> shift) & 255));
                        maxDelta = Math.max(maxDelta, delta); compared++;
                        if (delta != 0) differing++;
                        // float32 BLAS/NumPy versus Java pow can differ at a half-code boundary.
                        if (delta > 1) throw new AssertionError(fields[0] + " pixel " + p + " channel " + shift
                            + " expected=" + Integer.toHexString(expected[p]) + " actual=" + Integer.toHexString(actual[p]));
                    }
                }
                if (fields[0].equals("baseline_identity_controls") && !Arrays.equals(actual, input))
                    throw new AssertionError("Baseline must be bit-exact identity");
                if (s.grain > 0 && !Arrays.equals(actual, engine.render(input, w, h, s, ow, oh)))
                    throw new AssertionError("Grain is nondeterministic");
                cases++;
            }
        }
        FilmEngine.Settings settings = new FilmEngine.Settings();
        int[] tiny = {0xff123456};
        boolean cancelled = false;
        try { engine.render(tiny, 1, 1, settings, 1, 1, () -> true); }
        catch (CancellationException expected) { cancelled = true; }
        if (!cancelled) throw new AssertionError("Cancellation was ignored");
        boolean rejected = false;
        settings.styleId = "unrecognized";
        try { engine.render(tiny, 1, 1, settings, 1, 1); }
        catch (IllegalArgumentException expected) { rejected = true; }
        if (!rejected) throw new AssertionError("Unknown profile was accepted");
        byte[] damaged = Files.readAllBytes(new File(args[0], "baseline/curve.json").toPath());
        damaged[0] ^= 1;
        FilmEngine corrupt = new FilmEngine(path -> new ByteArrayInputStream(damaged));
        rejected = false;
        try { corrupt.render(tiny, 1, 1, new FilmEngine.Settings(), 1, 1); }
        catch (IllegalStateException expected) { rejected = true; }
        if (!rejected) throw new AssertionError("Damaged asset was accepted");
        System.out.println("PASS: " + cases + " desktop parity cases; " + compared + " channels; maximum error "
            + maxDelta + "/255 (" + differing + " differing channels). Identity, determinism, cancellation, and asset integrity passed.");
    }

    private static int[] pixels(String source) {
        String[] values = source.split(",");
        int[] result = new int[values.length];
        for (int i = 0; i < result.length; i++) result[i] = (int) Long.parseLong(values[i], 16);
        return result;
    }
}
