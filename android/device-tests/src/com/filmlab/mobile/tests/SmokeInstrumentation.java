package com.filmlab.mobile.tests;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.media.ExifInterface;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import com.filmlab.mobile.FilmEngine;
import com.filmlab.mobile.ImageFiles;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.concurrent.CancellationException;

/** Framework-only device checks; invoke via adb shell am instrument -w. */
public final class SmokeInstrumentation extends Instrumentation {
    private int assertions;
    private final StringBuilder report = new StringBuilder();

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            Context target = getTargetContext();
            File directory = new File(target.getCacheDir(), "device-smoke-tests");
            if (!directory.isDirectory() && !directory.mkdirs()) throw new AssertionError("Cannot create fixture directory");
            testEngine(target);
            testTransparency(target, directory);
            testOrientations(target, directory);
            File photo = testResizeAndSave(target, directory);
            testActivity(photo);
            result.putString("stream", "\nPASS: " + assertions + " assertions\n" + report);
            result.putInt("assertions", assertions);
            result.putString("result", "PASS");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable failure) {
            result.putString("stream", "\nFAIL after " + assertions + " assertions\n" + report + Log.getStackTraceString(failure));
            result.putString("result", "FAIL");
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private void testEngine(Context context) {
        FilmEngine engine = new FilmEngine(path -> context.getAssets().open("profiles/" + path));
        int[] input = {0xff000000, 0xffffffff, 0xffad6345, 0xff2266aa, 0xff51a846, 0xff777777};
        FilmEngine.Settings settings = new FilmEngine.Settings();
        int[] baseline = engine.render(input, 3, 2, settings, 3, 2);
        check(Arrays.equals(input, baseline), "baseline identity on Android runtime");
        for (String id : new String[]{"documentary", "negative", "cinema", "vivid", "monochrome"}) {
            settings.styleId = id;
            int[] styled = engine.render(input, 3, 2, settings, 3, 2);
            check(!Arrays.equals(input, styled), id + " asset loads and changes colors");
            if ("monochrome".equals(id)) {
                for (int p : styled) check(Color.red(p) == Color.green(p) && Color.red(p) == Color.blue(p), "neutral monochrome");
            }
        }
        settings.grain = 100;
        check(Arrays.equals(engine.render(input, 3, 2, settings, 3, 2),
                engine.render(input, 3, 2, settings, 3, 2)), "deterministic grain on Android runtime");
        boolean cancelled = false;
        try { engine.render(input, 3, 2, settings, 3, 2, () -> true); }
        catch (CancellationException expected) { cancelled = true; }
        check(cancelled, "cancel callback honored");
        report.append("Bundled profiles, grain, and cancellation passed.\n");
    }

    private void testTransparency(Context context, File directory) throws Exception {
        Bitmap source = Bitmap.createBitmap(3, 1, Bitmap.Config.ARGB_8888);
        source.setPixel(0, 0, Color.TRANSPARENT);
        source.setPixel(1, 0, Color.RED);
        source.setPixel(2, 0, 0x8000ff00);
        File png = new File(directory, "alpha.png");
        write(source, png, Bitmap.CompressFormat.PNG);
        source.recycle();
        ImageFiles.Loaded loaded = ImageFiles.load(context, Uri.fromFile(png), 1280);
        try {
            check(loaded.bitmap.getWidth() == 3 && loaded.bitmap.getHeight() == 1, "small image is not upscaled");
            check(loaded.originalWidth == 3 && loaded.originalHeight == 1, "small source dimensions retained");
            check(!loaded.bitmap.hasAlpha(), "output is marked opaque");
            colorClose(loaded.bitmap.getPixel(0, 0), Color.WHITE, 0, "transparent pixels composite onto white");
            colorClose(loaded.bitmap.getPixel(1, 0), Color.RED, 0, "opaque pixels preserved");
            colorClose(loaded.bitmap.getPixel(2, 0), 0xff7fff7f, 1, "half-alpha green composites onto white");
            check(loaded.bitmap.getColorSpace() != null && loaded.bitmap.getColorSpace().isSrgb(), "decoded bitmap is sRGB");
        } finally { loaded.bitmap.recycle(); }
        report.append("PNG transparency, sRGB, and no-upscale passed.\n");
    }

    private void testOrientations(Context context, File directory) throws Exception {
        final int[] colors = {Color.RED, Color.GREEN, Color.BLUE, Color.YELLOW};
        final int[][] corners = {
            {0, 1, 2, 3}, {1, 0, 3, 2}, {3, 2, 1, 0}, {2, 3, 0, 1},
            {0, 2, 1, 3}, {2, 0, 3, 1}, {3, 1, 2, 0}, {1, 3, 0, 2}
        };
        Bitmap source = Bitmap.createBitmap(80, 48, Bitmap.Config.ARGB_8888);
        for (int y = 0; y < 48; y++) for (int x = 0; x < 80; x++)
            source.setPixel(x, y, colors[(y >= 24 ? 2 : 0) + (x >= 40 ? 1 : 0)]);
        try {
            for (int orientation = 1; orientation <= 8; orientation++) {
                File jpeg = new File(directory, "orientation-" + orientation + ".jpg");
                write(source, jpeg, Bitmap.CompressFormat.JPEG);
                ExifInterface exif = new ExifInterface(jpeg.getAbsolutePath());
                exif.setAttribute(ExifInterface.TAG_ORIENTATION, Integer.toString(orientation));
                exif.saveAttributes();
                ImageFiles.Loaded loaded = ImageFiles.load(context, Uri.fromFile(jpeg), 1280);
                Bitmap image = loaded.bitmap;
                try {
                    int width = orientation >= 5 ? 48 : 80, height = orientation >= 5 ? 80 : 48;
                    check(image.getWidth() == width && image.getHeight() == height, "EXIF " + orientation + " output dimensions");
                    check(loaded.originalWidth == width && loaded.originalHeight == height, "EXIF " + orientation + " original dimensions");
                    for (int corner = 0; corner < 4; corner++) {
                        int x = corner % 2 == 0 ? width / 4 : width * 3 / 4;
                        int y = corner / 2 == 0 ? height / 4 : height * 3 / 4;
                        colorClose(image.getPixel(x, y), colors[corners[orientation - 1][corner]], 15,
                                "EXIF " + orientation + " corner " + corner);
                    }
                } finally { image.recycle(); }
            }
            testArwOrientation(context, directory, source);
        } finally { source.recycle(); }
        report.append("All eight JPEG EXIF orientations and oriented metadata passed.\n");
    }

    private void testArwOrientation(Context context, File directory, Bitmap source) throws Exception {
        File embedded = new File(directory, "embedded.jpg");
        write(source, embedded, Bitmap.CompressFormat.JPEG);
        byte[] jpeg = Files.readAllBytes(embedded.toPath());
        ByteBuffer header = ByteBuffer.allocate(128).order(ByteOrder.LITTLE_ENDIAN);
        header.putShort((short) 0x4949).putShort((short) 42).putInt(8);
        header.putShort((short) 3);
        header.putShort((short) 274).putShort((short) 3).putInt(1).putShort((short) 6).putShort((short) 0);
        header.putShort((short) 513).putShort((short) 4).putInt(1).putInt(128);
        header.putShort((short) 514).putShort((short) 4).putInt(1).putInt(jpeg.length);
        header.putInt(0);
        File raw = new File(directory, "synthetic.arw");
        try (FileOutputStream output = new FileOutputStream(raw)) {
            output.write(header.array()); output.write(jpeg);
        }
        ImageFiles.Loaded loaded = ImageFiles.load(context, Uri.fromFile(raw), 1280);
        try {
            Bitmap image = loaded.bitmap;
            check(image.getWidth() == 48 && image.getHeight() == 80, "ARW outer orientation swaps JPEG dimensions");
            check(loaded.originalWidth == 48 && loaded.originalHeight == 80, "ARW original dimensions are oriented");
            check(loaded.notice.contains("ARW"), "ARW preview limitation is reported");
            colorClose(image.getPixel(12, 20), Color.BLUE, 15, "ARW orientation top left");
            colorClose(image.getPixel(36, 20), Color.RED, 15, "ARW orientation top right");
            colorClose(image.getPixel(12, 60), Color.YELLOW, 15, "ARW orientation bottom left");
            colorClose(image.getPixel(36, 60), Color.GREEN, 15, "ARW orientation bottom right");
        } finally { loaded.bitmap.recycle(); }
        report.append("Synthetic Sony ARW JPEG extraction and outer EXIF rotation passed.\n");
    }

    private File testResizeAndSave(Context context, File directory) throws Exception {
        Bitmap source = Bitmap.createBitmap(2000, 1000, Bitmap.Config.ARGB_8888);
        int[] row = new int[2000];
        for (int y = 0; y < 1000; y++) {
            for (int x = 0; x < 2000; x++) row[x] = Color.rgb(x * 255 / 1999, y * 255 / 999, (x + y) * 255 / 2998);
            source.setPixels(row, 0, 2000, 0, y, 2000, 1);
        }
        File jpeg = new File(directory, "source.jpg");
        write(source, jpeg, Bitmap.CompressFormat.JPEG);
        source.recycle();
        ImageFiles.Loaded loaded = ImageFiles.load(context, Uri.fromFile(jpeg), 1280);
        try {
            check(loaded.bitmap.getWidth() == 1280 && loaded.bitmap.getHeight() == 640, "long edge resized to 1280");
            check(loaded.originalWidth == 2000 && loaded.originalHeight == 1000, "source dimensions retained for grain");
            File saved = new File(directory, "export.jpg");
            ImageFiles.saveJpeg(context, Uri.fromFile(saved), loaded.bitmap);
            check(saved.length() > 1000, "JPEG output written");
            Bitmap decoded = BitmapFactory.decodeFile(saved.getAbsolutePath());
            try {
                check(decoded != null && decoded.getWidth() == 1280 && decoded.getHeight() == 640, "saved JPEG decodes with correct size");
            } finally { if (decoded != null) decoded.recycle(); }
        } finally { loaded.bitmap.recycle(); }
        report.append("Resize and JPEG save/reload passed.\n");
        return jpeg;
    }

    private void testActivity(File photo) throws Exception {
        Intent launch = new Intent(Intent.ACTION_MAIN);
        launch.setClassName("com.filmlab.mobile", "com.filmlab.mobile.MainActivity");
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Activity activity = startActivitySync(launch);
        waitForIdleSync();
        check(activity != null && !activity.isFinishing(), "production launcher Activity starts");
        Method load = activity.getClass().getDeclaredMethod("loadSource", Uri.class, boolean.class);
        load.setAccessible(true);
        Field original = activity.getClass().getDeclaredField("original");
        Field edited = activity.getClass().getDeclaredField("edited");
        original.setAccessible(true); edited.setAccessible(true);
        Throwable[] error = new Throwable[1];
        runOnMainSync(() -> {
            try { load.invoke(activity, Uri.fromFile(photo), true); }
            catch (Throwable failure) { error[0] = failure; }
        });
        if (error[0] != null) throw new AssertionError("Activity import failed", error[0]);
        boolean[] ready = {false};
        long deadline = System.currentTimeMillis() + 20000;
        while (!ready[0] && System.currentTimeMillis() < deadline) {
            runOnMainSync(() -> {
                try { ready[0] = original.get(activity) != null && edited.get(activity) != null; }
                catch (Throwable failure) { error[0] = failure; }
            });
            if (error[0] != null) throw new AssertionError("Activity state check failed", error[0]);
            if (!ready[0]) Thread.sleep(100);
        }
        check(ready[0], "production Activity import and asynchronous preview completes");
        report.append("Production Activity startup, import, and preview passed.\n");
    }

    private void write(Bitmap bitmap, File target, Bitmap.CompressFormat format) throws Exception {
        try (FileOutputStream output = new FileOutputStream(target)) {
            check(bitmap.compress(format, 100, output), "fixture encoding");
        }
    }
    private void colorClose(int actual, int expected, int tolerance, String label) {
        check(Color.alpha(actual) == 255 && Math.abs(Color.red(actual) - Color.red(expected)) <= tolerance
                && Math.abs(Color.green(actual) - Color.green(expected)) <= tolerance
                && Math.abs(Color.blue(actual) - Color.blue(expected)) <= tolerance,
                label + ": actual=" + Integer.toHexString(actual) + " expected=" + Integer.toHexString(expected));
    }
    private void check(boolean condition, String label) {
        assertions++;
        if (!condition) throw new AssertionError(label);
    }
}
