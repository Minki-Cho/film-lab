package com.filmlab.mobile;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.util.Arrays;

/** Bounded TIFF parser checks, with optional local camera samples. */
public final class ArwPreviewTest {
    private static int assertions;
    private static void check(boolean value, String label) {
        assertions++;
        if (!value) throw new AssertionError(label);
    }
    public static void main(String[] args) throws Exception {
        File dir = new File(args[0]);
        dir.mkdirs();
        byte[] jpeg = {(byte) 255, (byte) 216, 10, 20, 30, (byte) 255, (byte) 217};
        for (ByteOrder order : new ByteOrder[] {ByteOrder.LITTLE_ENDIAN, ByteOrder.BIG_ENDIAN}) {
            ByteBuffer bytes = ByteBuffer.allocate(256).order(order);
            bytes.putShort((short) (order == ByteOrder.LITTLE_ENDIAN ? 0x4949 : 0x4d4d));
            bytes.putShort((short) 42).putInt(8);
            bytes.putShort((short) 3);
            bytes.putShort((short) 274).putShort((short) 3).putInt(1).putShort((short) 6).putShort((short) 0);
            bytes.putShort((short) 513).putShort((short) 4).putInt(1).putInt(128);
            bytes.putShort((short) 514).putShort((short) 4).putInt(1).putInt(jpeg.length);
            bytes.putInt(8); // Self-referential next-IFD must terminate.
            bytes.position(128); bytes.put(jpeg);
            File raw = new File(dir, "synthetic-" + order + ".arw");
            File out = new File(dir, "synthetic-" + order + ".jpg");
            Files.write(raw.toPath(), bytes.array());
            ImageFiles.ArwPreview preview = new ImageFiles.ArwPreview(raw);
            check(preview.orientation == 6, "outer EXIF orientation");
            preview.extract(out);
            check(Arrays.equals(jpeg, Files.readAllBytes(out.toPath())), "exact JPEG extraction");
            bytes.putInt(42, Integer.MAX_VALUE); // Out-of-bounds JPEG length.
            Files.write(raw.toPath(), bytes.array());
            try { new ImageFiles.ArwPreview(raw); throw new AssertionError("invalid span accepted"); }
            catch (IOException expected) { assertions++; }
        }
        File invalid = new File(dir, "invalid.arw");
        Files.write(invalid.toPath(), new byte[] {1, 2, 3, 4, 5, 6, 7, 8});
        try { new ImageFiles.ArwPreview(invalid); throw new AssertionError("invalid header accepted"); }
        catch (IOException expected) { assertions++; }
        for (int i = 1; i < args.length; i++) {
            File source = new File(args[i]);
            ImageFiles.ArwPreview preview = new ImageFiles.ArwPreview(source);
            File out = new File(dir, source.getName() + ".jpg");
            preview.extract(out);
            check(out.length() > 100000, "full preview instead of tiny thumbnail: " + source.getName());
            System.out.println(source.getName() + ": extracted " + out.length() + " bytes; orientation=" + preview.orientation);
        }
        System.out.println("ARW parser: " + assertions + " assertions passed");
    }
}
