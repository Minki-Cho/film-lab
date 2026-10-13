package com.filmlab.mobile;

import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorSpace;
import android.graphics.ImageDecoder;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.net.Uri;
import android.provider.OpenableColumns;
import java.io.*;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/** Read-only source access and bounded, colour-managed Android image decoding. */
public final class ImageFiles {
    private ImageFiles() {}
    private static final long MAX_RAW_BYTES = 512L * 1024 * 1024;

    public static final class Loaded {
        public Bitmap bitmap;
        public int originalWidth, originalHeight;
        public String name, notice;
    }

    public static Loaded load(Context context, Uri uri, int maxEdge) throws Exception {
        if (maxEdge < 1 || maxEdge > 3840) throw new IOException("지원하지 않는 출력 크기입니다.");
        Loaded result = new Loaded();
        result.name = displayName(context, uri);
        result.notice = "";
        String lower = result.name.toLowerCase(Locale.ROOT);
        String mime = context.getContentResolver().getType(uri);
        if (lower.endsWith(".tif") || lower.endsWith(".tiff") || "image/tiff".equals(mime))
            throw new IOException("TIFF는 PC 버전에서 편집해 주세요. 모바일은 JPEG·PNG·WebP·HEIC 및 ARW 내장 JPEG를 지원합니다.");
        File raw = null, jpeg = null;
        try {
            ImageDecoder.Source source;
            int outerOrientation = 1;
            if (lower.endsWith(".arw") || (mime != null && mime.toLowerCase(Locale.ROOT).contains("sony"))) {
                raw = File.createTempFile("arw-source-", ".tmp", context.getCacheDir());
                try (InputStream input = context.getContentResolver().openInputStream(uri);
                     OutputStream output = new FileOutputStream(raw)) {
                    if (input == null) throw new IOException("원본을 열 수 없습니다.");
                    transfer(input, output, MAX_RAW_BYTES);
                }
                ArwPreview preview = new ArwPreview(raw);
                jpeg = File.createTempFile("arw-preview-", ".jpg", context.getCacheDir());
                preview.extract(jpeg);
                ExifInterface jpegExif = new ExifInterface(jpeg.getAbsolutePath());
                // ImageDecoder handles a rotated JPEG's own orientation automatically.
                // A preview tagged normal (or untagged) still needs the outer RAW orientation.
                if (jpegExif.getAttributeInt(ExifInterface.TAG_ORIENTATION, 0) <= 1)
                    outerOrientation = preview.orientation;
                source = ImageDecoder.createSource(jpeg);
                result.notice = "ARW 내장 JPEG 편집 · RAW 원본 현상이 아닙니다";
            } else {
                source = ImageDecoder.createSource(context.getContentResolver(), uri);
            }
            result.bitmap = ImageDecoder.decodeBitmap(source, (decoder, info, src) -> {
                int width = info.getSize().getWidth(), height = info.getSize().getHeight();
                if (width < 1 || height < 1 || (long) width * height > 300_000_000L)
                    throw new IllegalArgumentException("사진의 크기가 너무 큽니다.");
                result.originalWidth = width;
                result.originalHeight = height;
                float ratio = Math.min(1f, (float) maxEdge / Math.max(width, height));
                decoder.setTargetSize(Math.max(1, Math.round(width * ratio)), Math.max(1, Math.round(height * ratio)));
                decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);
                decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB));
            });
            if (outerOrientation > 1 && outerOrientation <= 8) {
                Bitmap turned = orient(result.bitmap, outerOrientation);
                if (turned != result.bitmap) result.bitmap.recycle();
                result.bitmap = turned;
                if (outerOrientation >= 5) {
                    int swap = result.originalWidth;
                    result.originalWidth = result.originalHeight;
                    result.originalHeight = swap;
                }
            }
            if (result.bitmap.hasAlpha() || result.bitmap.getConfig() != Bitmap.Config.ARGB_8888) {
                Bitmap opaque = Bitmap.createBitmap(result.bitmap.getWidth(), result.bitmap.getHeight(), Bitmap.Config.ARGB_8888);
                Canvas canvas = new Canvas(opaque);
                canvas.drawColor(Color.WHITE);
                canvas.drawBitmap(result.bitmap, 0, 0, null);
                opaque.setHasAlpha(false);
                result.bitmap.recycle();
                result.bitmap = opaque;
            }
            return result;
        } catch (Exception | OutOfMemoryError e) {
            if (result.bitmap != null && !result.bitmap.isRecycled()) result.bitmap.recycle();
            if (e instanceof OutOfMemoryError) throw new IOException("메모리가 부족합니다. 더 작은 사진으로 다시 시도해 주세요.", e);
            throw e;
        } finally {
            if (jpeg != null) jpeg.delete();
            if (raw != null) raw.delete();
        }
    }

    public static void saveJpeg(Context context, Uri destination, Bitmap image) throws Exception {
        File pending = File.createTempFile("film-export-", ".jpg", context.getCacheDir());
        try {
            try (OutputStream out = new FileOutputStream(pending)) {
                if (!image.compress(Bitmap.CompressFormat.JPEG, 95, out)) throw new IOException("JPEG 인코딩에 실패했습니다.");
            }
            try (InputStream input = new FileInputStream(pending);
                 OutputStream output = context.getContentResolver().openOutputStream(destination, "wt")) {
                if (output == null) throw new IOException("저장 위치를 열 수 없습니다.");
                transfer(input, output, Long.MAX_VALUE);
            }
        } finally { pending.delete(); }
    }

    private static String displayName(Context context, Uri uri) {
        try (Cursor cursor = context.getContentResolver().query(uri, new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String name = cursor.getString(0);
                if (name != null && !name.trim().isEmpty()) return name;
            }
        } catch (Exception ignored) {}
        String tail = uri.getLastPathSegment();
        return tail == null ? "photo" : tail;
    }

    private static void transfer(InputStream input, OutputStream output, long limit) throws IOException {
        byte[] bytes = new byte[64 * 1024];
        long size = 0;
        int count;
        while ((count = input.read(bytes)) != -1) {
            if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("작업을 취소했습니다.");
            size += count;
            if (size > limit) throw new IOException("ARW 파일은 512 MB 이하만 지원합니다.");
            output.write(bytes, 0, count);
        }
        output.flush();
    }

    private static Bitmap orient(Bitmap bitmap, int orientation) {
        Matrix matrix = new Matrix();
        switch (orientation) {
            case 2: matrix.setScale(-1, 1); break;
            case 3: matrix.setRotate(180); break;
            case 4: matrix.setScale(1, -1); break;
            case 5: matrix.setRotate(90); matrix.postScale(-1, 1); break;
            case 6: matrix.setRotate(90); break;
            case 7: matrix.setRotate(270); matrix.postScale(-1, 1); break;
            case 8: matrix.setRotate(270); break;
            default: return bitmap;
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(), matrix, true);
    }

    /** Reads TIFF IFD JPEG offsets, never treating sensor bytes as a developed image. */
    static final class ArwPreview {
        private final File file;
        private boolean little;
        private long jpegOffset, jpegLength, fileLength;
        int orientation = 1;
        private final Set<Long> visited = new HashSet<>();

        ArwPreview(File file) throws IOException {
            this.file = file;
            try (RandomAccessFile input = new RandomAccessFile(file, "r")) {
                fileLength = input.length();
                int order = input.readUnsignedShort();
                if (order != 0x4949 && order != 0x4d4d) throw new IOException("유효한 ARW/TIFF 헤더가 없습니다.");
                little = order == 0x4949;
                if (u16(input) != 42) throw new IOException("지원하지 않는 ARW 구조입니다.");
                long first = u32(input);
                visit(input, first, 0);
                if (jpegLength == 0) throw new IOException("이 ARW에는 읽을 수 있는 JPEG 미리보기가 없습니다. PC 버전에서 현상해 주세요.");
            }
        }

        private int u16(RandomAccessFile in) throws IOException {
            int a = in.readUnsignedByte(), b = in.readUnsignedByte();
            return little ? a | b << 8 : a << 8 | b;
        }
        private long u32(RandomAccessFile in) throws IOException {
            long a = u16(in), b = u16(in);
            return little ? a | b << 16 : a << 16 | b;
        }
        private boolean valid(long offset, long size) {
            return offset >= 8 && size > 0 && offset <= fileLength && size <= fileLength - offset;
        }
        private void visit(RandomAccessFile in, long offset, int depth) throws IOException {
            if (depth > 12 || visited.size() >= 128 || !valid(offset, 2) || !visited.add(offset)) return;
            in.seek(offset);
            int count = u16(in);
            if (count > 4096 || !valid(offset + 2, (long) count * 12 + 4)) return;
            long previewOffset = 0, previewLength = 0;
            for (int i = 0; i < count; i++) {
                long entry = offset + 2 + i * 12L;
                in.seek(entry);
                int tag = u16(in), type = u16(in);
                long n = u32(in);
                long value = u32(in);
                if (type == 3 && n == 1) value = little ? value & 65535 : value >>> 16;
                if ((type == 3 || type == 4) && n == 1) {
                    if (tag == 274 && depth == 0 && value >= 1 && value <= 8) orientation = (int) value;
                    if (tag == 513) previewOffset = value;
                    if (tag == 514) previewLength = value;
                }
                if ((tag == 330 || tag == 34665) && type == 4 && n > 0 && n <= 64) {
                    if (n == 1) visit(in, value, depth + 1);
                    else if (valid(value, n * 4)) {
                        for (int j = 0; j < n; j++) {
                            in.seek(value + j * 4L);
                            long sub = u32(in);
                            visit(in, sub, depth + 1);
                        }
                    }
                }
            }
            if (previewLength > jpegLength && valid(previewOffset, previewLength)) {
                in.seek(previewOffset);
                if (in.readUnsignedShort() == 0xffd8) { jpegOffset = previewOffset; jpegLength = previewLength; }
            }
            in.seek(offset + 2 + count * 12L);
            long next = u32(in);
            visit(in, next, depth + 1);
        }
        void extract(File target) throws IOException {
            try (RandomAccessFile in = new RandomAccessFile(file, "r"); OutputStream out = new FileOutputStream(target)) {
                in.seek(jpegOffset);
                byte[] buffer = new byte[64 * 1024];
                long left = jpegLength;
                while (left > 0) {
                    int amount = in.read(buffer, 0, (int) Math.min(buffer.length, left));
                    if (amount <= 0) throw new EOFException("ARW 미리보기가 잘렸습니다.");
                    out.write(buffer, 0, amount);
                    left -= amount;
                }
            }
        }
    }
}
