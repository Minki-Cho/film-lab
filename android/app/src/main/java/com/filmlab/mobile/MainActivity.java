package com.filmlab.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;

/** An offline editor. Storage access is always granted by the system document picker. */
public final class MainActivity extends Activity {
    private static final int OPEN_IMAGE = 40;
    private static final int SAVE_IMAGE = 41;
    private static final int BACKGROUND = Color.rgb(18, 20, 22);
    private static final int PANEL = Color.rgb(28, 31, 34);
    private static final int BORDER = Color.rgb(53, 57, 62);
    private static final int WHITE = Color.rgb(245, 244, 240);
    private static final int MUTED = Color.rgb(177, 182, 185);
    private static final int AMBER = Color.rgb(237, 188, 115);
    private static final String[] STYLE_IDS = {
            "baseline", "documentary", "negative", "cinema", "vivid", "monochrome"
    };
    private static final String[] STYLE_NAMES = {
            "기본", "다큐멘터리", "네거티브", "시네마", "비비드", "모노크롬"
    };
    private static final String[] STYLE_NOTES = {
            "깨끗한 색과 자연스러운 기본 톤",
            "차분한 색감으로 일상의 분위기를",
            "깊은 대비와 네거티브 필름의 색감",
            "영화 장면처럼 부드럽고 깊은 색",
            "풍부한 채도로 선명하게 남기는 순간",
            "빛과 질감에 집중하는 흑백"
    };

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final AtomicInteger generation = new AtomicInteger();
    private final AtomicInteger loadGeneration = new AtomicInteger();
    private final ArrayDeque<FilmEngine.Settings> history = new ArrayDeque<>();
    private final ArrayList<Slider> sliders = new ArrayList<>();
    private final ArrayList<Button> styleButtons = new ArrayList<>();
    private FilmEngine engine;
    private FilmEngine.Settings settings = new FilmEngine.Settings();
    private Bitmap original;
    private Bitmap edited;
    private Uri sourceUri;
    private Uri loadingSourceUri;
    private String sourceName = "사진";
    private String sourceNotice = "";
    private int originalWidth;
    private int originalHeight;
    private volatile boolean destroyed;
    private boolean loading;
    private boolean exporting;
    private boolean exportPickerOpen;
    private boolean comparing;
    private boolean previewDirty = true;
    private boolean updatingWidgets;
    private boolean suppressCompareClick;
    private Runnable pendingPreview;
    private Uri exportSource;
    private FilmEngine.Settings exportSettings;
    private int exportEdge = 2048;
    private Button openButton;
    private Button saveButton;
    private Button compareButton;
    private Button undoButton;
    private Button resetButton;
    private ImageView photo;
    private TextView emptyPreview;
    private TextView compareBadge;
    private TextView fileLabel;
    private TextView statusLabel;
    private TextView styleNote;
    private TextView noticeLabel;
    private ProgressBar progress;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        Window window = getWindow();
        window.setStatusBarColor(BACKGROUND);
        window.setNavigationBarColor(BACKGROUND);
        if (android.os.Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(false);
        window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        }
        engine = new FilmEngine(path -> getAssets().open("profiles/" + path));
        if (state != null) {
            settings = readSettings(state.getBundle("settings"));
            sourceName = state.getString("sourceName", "사진");
            String source = state.getString("sourceUri");
            if (source != null) sourceUri = Uri.parse(source);
            exportPickerOpen = state.getBoolean("exportPickerOpen", false);
            String export = state.getString("exportSource");
            if (export != null) exportSource = Uri.parse(export);
            exportSettings = readSettings(state.getBundle("exportSettings"));
            exportEdge = state.getInt("exportEdge", 2048);
        }
        buildUi();
        syncWidgets();
        if (sourceUri != null) loadSource(sourceUri, false);
        else setStatus("사진을 열어 나만의 필름 톤을 만들어 보세요.", false);
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BACKGROUND);
        // Android 15 enforces edge-to-edge. Insets also work on the older supported releases.
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int left, top, right, bottom;
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                left = bars.left; top = bars.top; right = bars.right; bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft();
                top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(left, top, right, bottom);
            return insets;
        });
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        LinearLayout content = column();
        content.setPadding(dp(20), dp(4), dp(20), dp(28));
        scroll.addView(content, new ScrollView.LayoutParams(-1, -2));
        boolean sideBySide = getResources().getConfiguration().orientation
                == android.content.res.Configuration.ORIENTATION_LANDSCAPE
                || getResources().getConfiguration().screenWidthDp >= 680;
        LinearLayout shell = sideBySide ? row() : column();
        LinearLayout hero = column();
        hero.setPadding(dp(20), dp(12), dp(20), dp(8));
        if (sideBySide) {
            ScrollView heroScroll = new ScrollView(this);
            heroScroll.addView(hero, new ScrollView.LayoutParams(-1, -2));
            shell.addView(heroScroll, new LinearLayout.LayoutParams(0, -1, 1));
            shell.addView(scroll, new LinearLayout.LayoutParams(0, -1, 1));
        } else {
            shell.addView(hero, new LinearLayout.LayoutParams(-1, -2));
            shell.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        }
        root.addView(shell, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        root.requestApplyInsets();

        LinearLayout heading = row();
        LinearLayout brand = column();
        TextView wordmark = text("FILM LAB", 26, WHITE);
        wordmark.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        wordmark.setLetterSpacing(.13f);
        brand.addView(wordmark);
        TextView tagline = text("당신의 순간에, 필름의 온도", 12, MUTED);
        add(brand, tagline, 0, 5);
        heading.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
        Button about = button("안내", false);
        about.setContentDescription("앱 사용 안내 및 지원 형식");
        about.setOnClickListener(v -> showAbout());
        heading.addView(about, new LinearLayout.LayoutParams(-2, -2));
        hero.addView(heading);

        LinearLayout meta = row();
        TextView offline = text("●  OFFLINE STUDIO", 10, AMBER);
        offline.setLetterSpacing(.08f);
        meta.addView(offline, new LinearLayout.LayoutParams(0, -2, 1));
        TextView local = text("기기 안에서만 작업", 11, MUTED);
        meta.addView(local);
        add(hero, meta, 0, 14);

        FrameLayout preview = new FrameLayout(this) {
            @Override protected void onMeasure(int widthSpec, int heightSpec) {
                int width = View.MeasureSpec.getSize(widthSpec);
                int screenCap = dp(Math.round(getResources().getConfiguration().screenHeightDp
                        * (sideBySide ? .44f : .30f)));
                int height = Math.max(dp(110), Math.min(screenCap, Math.min(dp(280), width * 3 / 4)));
                super.onMeasure(widthSpec, View.MeasureSpec.makeMeasureSpec(height,
                        View.MeasureSpec.EXACTLY));
            }
        };
        preview.setBackground(shape(Color.rgb(10, 12, 13), 16, BORDER));
        preview.setClipToOutline(true);
        photo = new ImageView(this);
        photo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        photo.setContentDescription("선택한 사진의 보정 미리보기");
        preview.addView(photo, new FrameLayout.LayoutParams(-1, -1));
        emptyPreview = text(sideBySide ? "사진 한 장에서 시작하는\n작은 필름 스튜디오"
                : "F / L\n\n사진 한 장에서 시작하는\n작은 필름 스튜디오", 16, MUTED);
        emptyPreview.setGravity(Gravity.CENTER);
        emptyPreview.setLineSpacing(dp(5), 1);
        preview.addView(emptyPreview, new FrameLayout.LayoutParams(-1, -1));
        compareBadge = text("원본", 12, WHITE);
        compareBadge.setPadding(dp(12), dp(6), dp(12), dp(6));
        compareBadge.setBackground(shape(0xcc121416, 9, Color.TRANSPARENT));
        compareBadge.setVisibility(View.GONE);
        FrameLayout.LayoutParams badgeParams = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        badgeParams.setMargins(dp(12), dp(12), dp(12), dp(12));
        preview.addView(compareBadge, badgeParams);
        add(hero, preview, 0, 12);

        fileLabel = text("아직 선택한 사진이 없습니다", 12, MUTED);
        fileLabel.setMaxLines(3);
        add(content, fileLabel, 0, 12);
        LinearLayout status = row();
        progress = new ProgressBar(this);
        progress.setIndeterminateTintList(ColorStateList.valueOf(AMBER));
        LinearLayout.LayoutParams progressParams = new LinearLayout.LayoutParams(dp(16), dp(16));
        progressParams.setMargins(0, 0, dp(8), 0);
        status.addView(progress, progressParams);
        statusLabel = text("", 12, MUTED);
        statusLabel.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        status.addView(statusLabel, new LinearLayout.LayoutParams(0, -2, 1));
        add(content, status, 0, 8);

        LinearLayout actions = row();
        openButton = button("사진 열기", false);
        openButton.setOnClickListener(v -> showImportOptions());
        saveButton = button("JPEG 저장", true);
        saveButton.setOnClickListener(v -> showExportOptions());
        actions.addView(openButton, weighted(0, 5));
        actions.addView(saveButton, weighted(5, 0));
        add(content, actions, 0, 16);
        compareButton = button("누르고 원본 비교", false);
        compareButton.setOnClickListener(v -> {
            if (!suppressCompareClick) showOriginal(!comparing);
        });
        View.OnTouchListener compareTouch = (v, event) -> {
            if (!v.isEnabled() || original == null || loading || exporting || exportPickerOpen) return false;
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    v.setPressed(true);
                    showOriginal(true);
                    return true;
                case MotionEvent.ACTION_UP:
                    v.setPressed(false);
                    showOriginal(false);
                    suppressCompareClick = true;
                    v.performClick();
                    suppressCompareClick = false;
                    return true;
                case MotionEvent.ACTION_CANCEL:
                    v.setPressed(false);
                    showOriginal(false);
                    return true;
                default:
                    return true;
            }
        };
        compareButton.setOnTouchListener(compareTouch);
        photo.setOnTouchListener(compareTouch);
        photo.setOnClickListener(v -> {
            if (!suppressCompareClick && original != null && !loading && !exporting && !exportPickerOpen) {
                showOriginal(!comparing);
            }
        });
        compareButton.setContentDescription("누르는 동안 원본 보기. 접근성 클릭으로 원본과 보정 전환");
        add(content, compareButton, 0, 4);

        noticeLabel = text("", 12, AMBER);
        noticeLabel.setLineSpacing(dp(3), 1);
        noticeLabel.setVisibility(View.GONE);
        add(content, noticeLabel, 0, 12);

        add(content, sectionTitle("01", "필름 스타일"), 0, 24);
        for (int rowIndex = 0; rowIndex < 2; rowIndex++) {
            LinearLayout styleRow = row();
            for (int col = 0; col < 3; col++) {
                final int index = rowIndex * 3 + col;
                Button style = button(STYLE_NAMES[index], false);
                style.setTextSize(12);
                style.setPadding(dp(5), dp(10), dp(5), dp(10));
                style.setOnClickListener(v -> {
                    if (!STYLE_IDS[index].equals(settings.styleId)) {
                        pushHistory(settings);
                        settings.styleId = STYLE_IDS[index];
                        syncWidgets();
                        requestPreview();
                    }
                });
                styleButtons.add(style);
                styleRow.addView(style, weighted(col == 0 ? 0 : 3, col == 2 ? 0 : 3));
            }
            add(content, styleRow, 0, rowIndex == 0 ? 12 : 3);
        }
        styleNote = text("", 12, MUTED);
        add(content, styleNote, 0, 10);

        LinearLayout adjustments = sectionTitle("02", "세밀한 조정");
        add(content, adjustments, 0, 28);
        LinearLayout historyRow = row();
        undoButton = button("되돌리기", false);
        undoButton.setOnClickListener(v -> {
            if (!history.isEmpty()) {
                settings = history.removeLast();
                syncWidgets();
                requestPreview();
            }
        });
        resetButton = button("전체 초기화", false);
        resetButton.setOnClickListener(v -> {
            FilmEngine.Settings defaults = new FilmEngine.Settings();
            if (!sameSettings(settings, defaults)) {
                pushHistory(settings);
                settings = defaults;
                syncWidgets();
                requestPreview();
            }
        });
        historyRow.addView(undoButton, weighted(0, 5));
        historyRow.addView(resetButton, weighted(5, 0));
        add(content, historyRow, 0, 10);
        LinearLayout adjustmentPanel = column();
        adjustmentPanel.setPadding(dp(15), dp(12), dp(15), dp(10));
        adjustmentPanel.setBackground(shape(PANEL, 15, BORDER));
        add(content, adjustmentPanel, 0, 10);
        addSlider(adjustmentPanel, "strength", "스타일 강도", 0, 100);
        addSlider(adjustmentPanel, "exposure", "노출", -20, 30);
        addSlider(adjustmentPanel, "warmth", "색온도 · 차갑게 / 따뜻하게", -100, 100);
        addSlider(adjustmentPanel, "tint", "색조 · 초록 / 자홍", -100, 100);
        addSlider(adjustmentPanel, "contrast", "대비", -100, 100);
        addSlider(adjustmentPanel, "saturation", "채도", -100, 100);
        addSlider(adjustmentPanel, "highlights", "하이라이트", -100, 100);
        addSlider(adjustmentPanel, "shadows", "그림자", -100, 100);
        addSlider(adjustmentPanel, "grain", "필름 입자", 0, 100);
        TextView footer = text("원본은 그대로 · 저장은 새 JPEG 파일로\nFILM LAB  /  MOBILE 1.0", 11, MUTED);
        footer.setGravity(Gravity.CENTER);
        footer.setLineSpacing(dp(7), 1);
        add(content, footer, 0, 25);
        updateEnabled();
    }

    private void showImportOptions() {
        if (loading || exporting || exportPickerOpen) return;
        new AlertDialog.Builder(this)
                .setTitle("사진 열기")
                .setItems(new String[]{"사진 선택 · JPEG / PNG / WebP / HEIC", "파일 선택 · Sony ARW 포함"},
                        (dialog, which) -> openDocument(which == 1))
                .setNegativeButton("취소", null)
                .show();
    }

    private void openDocument(boolean allFiles) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(allFiles ? "*/*" : "image/*");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try {
            startActivityForResult(intent, OPEN_IMAGE);
        } catch (ActivityNotFoundException e) {
            showError("파일 선택기를 열 수 없습니다", "기기에 파일 선택 앱이 있는지 확인해 주세요.");
        }
    }

    private void showExportOptions() {
        if (sourceUri == null || original == null || loading || exporting || exportPickerOpen) return;
        final int[] selected = {exportEdge == 3840 ? 1 : 0};
        new AlertDialog.Builder(this)
                .setTitle("JPEG로 저장")
                .setSingleChoiceItems(new String[]{"최대 2,048 px · 빠르고 가볍게", "최대 3,840 px · 더 큰 사진"},
                        selected[0], (dialog, which) -> selected[0] = which)
                .setMessage(null)
                .setPositiveButton("저장 위치 선택", (dialog, which) -> {
                    exportEdge = selected[0] == 1 ? 3840 : 2048;
                    exportSource = sourceUri;
                    exportSettings = settings.copy();
                    exportPickerOpen = true;
                    showOriginal(false);
                    updateEnabled();
                    Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("image/jpeg");
                    intent.putExtra(Intent.EXTRA_TITLE, exportName());
                    try {
                        startActivityForResult(intent, SAVE_IMAGE);
                    } catch (ActivityNotFoundException e) {
                        exportPickerOpen = false;
                        updateEnabled();
                        showError("저장 위치를 열 수 없습니다", "기기에 파일 선택 앱이 있는지 확인해 주세요.");
                    }
                })
                .setNegativeButton("취소", null)
                .show();
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == SAVE_IMAGE) {
            exportPickerOpen = false;
            if (resultCode == RESULT_OK && data != null && data.getData() != null
                    && exportSource != null && exportSettings != null) {
                exportTo(data.getData());
            } else {
                updateEnabled();
                if (previewDirty) requestPreview();
            }
        } else if (requestCode == OPEN_IMAGE && resultCode == RESULT_OK
                && data != null && data.getData() != null) {
            Uri uri = data.getData();
            try {
                int granted = data.getFlags() & Intent.FLAG_GRANT_READ_URI_PERMISSION;
                getContentResolver().takePersistableUriPermission(uri, granted);
            } catch (SecurityException ignored) {
                // Some document providers grant access for this session only.
            }
            loadSource(uri, true);
        }
    }

    private void loadSource(Uri uri, boolean newSelection) {
        cancelPendingPreview();
        generation.incrementAndGet();
        int token = loadGeneration.incrementAndGet();
        loading = true;
        loadingSourceUri = uri;
        showOriginal(false);
        updateEnabled();
        setStatus("사진을 불러오는 중…", true);
        worker.execute(() -> {
            try {
                ImageFiles.Loaded loaded = ImageFiles.load(getApplicationContext(), uri, 1280);
                main.post(() -> {
                    if (destroyed || token != loadGeneration.get()) {
                        loaded.bitmap.recycle();
                        return;
                    }
                    original = loaded.bitmap;
                    edited = null;
                    previewDirty = true;
                    sourceUri = uri;
                    loadingSourceUri = null;
                    sourceName = loaded.name == null ? "사진" : loaded.name;
                    sourceNotice = loaded.notice == null ? "" : loaded.notice;
                    originalWidth = loaded.originalWidth;
                    originalHeight = loaded.originalHeight;
                    loading = false;
                    emptyPreview.setVisibility(View.GONE);
                    photo.setImageBitmap(original);
                    fileLabel.setText(sourceName + "  ·  " + originalWidth + " × " + originalHeight);
                    noticeLabel.setText(sourceNotice);
                    noticeLabel.setVisibility(sourceNotice.isEmpty() ? View.GONE : View.VISIBLE);
                    if (newSelection) history.clear();
                    updateEnabled();
                    requestPreview();
                });
            } catch (Exception | OutOfMemoryError error) {
                main.post(() -> {
                    if (destroyed || token != loadGeneration.get()) return;
                    loading = false;
                    loadingSourceUri = null;
                    if (original == null) sourceUri = null;
                    updateEnabled();
                    setStatus("사진을 열지 못했습니다.", false);
                    showError("사진을 열지 못했습니다", friendlyError(error));
                });
            }
        });
    }

    private void requestPreview() {
        if (original == null || loading || exporting || exportPickerOpen || destroyed) return;
        previewDirty = true;
        cancelPendingPreview();
        final int token = generation.incrementAndGet();
        setStatus("필름 톤을 적용하는 중…", true);
        pendingPreview = () -> {
            pendingPreview = null;
            final Bitmap input = original;
            final FilmEngine.Settings snapshot = settings.copy();
            final int fullWidth = originalWidth;
            final int fullHeight = originalHeight;
            worker.execute(() -> {
                if (destroyed || token != generation.get()) return;
                try {
                    Bitmap result = render(input, snapshot, fullWidth, fullHeight, false,
                            () -> destroyed || token != generation.get());
                    main.post(() -> {
                        if (destroyed || token != generation.get()) {
                            result.recycle();
                            return;
                        }
                        edited = result;
                        previewDirty = false;
                        photo.setImageBitmap(comparing ? original : edited);
                        setStatus("미리보기 최대 1,280 px · 저장할 때 선택한 크기로 처리", false);
                    });
                } catch (CancellationException ignored) {
                    // A newer slider value or source has superseded this render.
                } catch (Exception | OutOfMemoryError error) {
                    main.post(() -> {
                        if (destroyed || token != generation.get()) return;
                        setStatus("미리보기 처리 실패 · 설정을 낮추거나 다시 열어 주세요.", false);
                        showError("미리보기를 만들지 못했습니다", friendlyError(error));
                    });
                }
            });
        };
        main.postDelayed(pendingPreview, 140);
    }

    private Bitmap render(Bitmap input, FilmEngine.Settings snapshot, int fullWidth, int fullHeight,
                          boolean releaseInput, BooleanSupplier cancelled)
            throws Exception {
        int width = input.getWidth();
        int height = input.getHeight();
        int[] pixels = new int[width * height];
        input.getPixels(pixels, 0, width, 0, 0, width, height);
        if (releaseInput) input.recycle();
        int[] output = engine.render(pixels, width, height, snapshot, fullWidth, fullHeight, cancelled);
        pixels = null;
        return Bitmap.createBitmap(output, width, height, Bitmap.Config.ARGB_8888);
    }

    private void exportTo(Uri destination) {
        if (destination.equals(exportSource)) {
            updateEnabled();
            showError("다른 파일로 저장해 주세요", "원본을 보호하기 위해 같은 파일에는 저장할 수 없습니다.");
            if (previewDirty) requestPreview();
            return;
        }
        cancelPendingPreview();
        generation.incrementAndGet();
        exporting = true;
        updateEnabled();
        setStatus("JPEG를 만드는 중… 앱을 열어 둔 채 기다려 주세요.", true);
        final Uri inputUri = exportSource;
        final FilmEngine.Settings snapshot = exportSettings.copy();
        final int maxEdge = exportEdge;
        // Prevent rotation from destroying an in-progress document write. Unlock immediately afterward.
        final int previousOrientation = getRequestedOrientation();
        setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LOCKED);
        worker.execute(() -> {
            Bitmap input = null;
            Bitmap result = null;
            try {
                ImageFiles.Loaded loaded = ImageFiles.load(getApplicationContext(), inputUri, maxEdge);
                input = loaded.bitmap;
                result = render(input, snapshot, loaded.originalWidth, loaded.originalHeight,
                        true, () -> false);
                ImageFiles.saveJpeg(getApplicationContext(), destination, result);
                final int savedWidth = result.getWidth();
                final int savedHeight = result.getHeight();
                main.post(() -> {
                    if (destroyed) return;
                    exporting = false;
                    updateEnabled();
                    setRequestedOrientation(previousOrientation);
                    setStatus("저장 완료 · " + savedWidth + " × " + savedHeight + " px JPEG", false);
                    Toast.makeText(this, "새 JPEG 파일을 저장했습니다", Toast.LENGTH_LONG).show();
                    if (previewDirty) requestPreview();
                });
            } catch (Exception | OutOfMemoryError error) {
                main.post(() -> {
                    if (destroyed) return;
                    exporting = false;
                    updateEnabled();
                    setRequestedOrientation(previousOrientation);
                    setStatus("저장하지 못했습니다. 저장 위치와 남은 공간을 확인해 주세요.", false);
                    showError("JPEG 저장 실패", friendlyError(error)
                            + "\n\n선택한 위치에 빈 파일이 남아 있다면 삭제한 후 다시 저장해 주세요.");
                    if (previewDirty) requestPreview();
                });
            } finally {
                if (input != null && !input.isRecycled()) input.recycle();
                if (result != null && result != input) result.recycle();
            }
        });
    }

    private void showOriginal(boolean show) {
        comparing = show && original != null;
        if (photo == null) return;
        photo.setImageBitmap(comparing || edited == null ? original : edited);
        photo.setContentDescription(comparing ? "보정 전 원본 사진. 클릭하면 보정 사진으로 전환"
                : "보정 사진. 누르는 동안 원본 보기, 접근성 클릭으로 원본과 보정 전환");
        compareBadge.setVisibility(comparing ? View.VISIBLE : View.GONE);
        compareButton.setText(comparing ? "원본 보는 중 · 놓으면 보정 사진" : "누르고 원본 비교");
        compareButton.setSelected(comparing);
    }

    private void updateEnabled() {
        if (openButton == null) return;
        boolean available = !loading && !exporting && !exportPickerOpen;
        boolean editable = available && original != null;
        openButton.setEnabled(available);
        saveButton.setEnabled(editable);
        compareButton.setEnabled(editable);
        undoButton.setEnabled(editable && !history.isEmpty());
        resetButton.setEnabled(editable);
        for (Button style : styleButtons) style.setEnabled(editable);
        for (Slider slider : sliders) slider.seek.setEnabled(editable);
        for (View v : new View[]{openButton, saveButton, compareButton, undoButton, resetButton}) {
            v.setAlpha(v.isEnabled() ? 1f : .4f);
        }
    }

    private void setStatus(String message, boolean busy) {
        statusLabel.setText(message);
        progress.setVisibility(busy ? View.VISIBLE : View.GONE);
    }

    private void showAbout() {
        new AlertDialog.Builder(this)
                .setTitle("FILM LAB · Mobile 1.0")
                .setMessage("사진을 열고 스타일과 보정값을 조절한 뒤 새 JPEG로 저장하세요. 원본 파일은 수정하지 않습니다. 사진은 서버로 전송되지 않습니다.\n\n"
                        + "지원: JPEG, PNG, WebP 및 기기에서 읽을 수 있는 HEIC. Sony ARW는 파일 안에 들어 있는 JPEG 미리보기만 사용합니다. 실제 RAW 현상은 지원하지 않습니다.\n\n"
                        + "미리보기는 긴 변 최대 1,280 px, 저장은 최대 2,048 / 3,840 px입니다. 작은 사진은 확대하지 않습니다. JPEG 저장 시 투명 영역은 합성되고 촬영 메타데이터는 보존되지 않습니다.\n\n"
                        + "모바일 버전에는 TIFF 16비트, 일괄 처리, 프로젝트/레시피 파일 기능이 없습니다. 스타일은 Film Lab 자체 제작이며 특정 필름 제품의 정확한 재현을 보장하지 않습니다.\n\n"
                        + "사진이나 원본 비교 버튼을 누르고 있으면 원본이 보입니다. 접근성 클릭은 비교 화면을 전환합니다.")
                .setPositiveButton("확인", null).show();
    }

    private void showError(String title, String message) {
        if (!destroyed && !isFinishing()) {
            new AlertDialog.Builder(this).setTitle(title).setMessage(message)
                    .setPositiveButton("확인", null).show();
        }
    }

    private String friendlyError(Throwable error) {
        if (error instanceof OutOfMemoryError) {
            return "사용 가능한 메모리가 부족합니다. 다른 앱을 닫거나 2,048 px로 저장해 주세요.";
        }
        if (error instanceof SecurityException) {
            return "이 파일에 접근할 수 없습니다. 사진 열기에서 파일을 다시 선택해 주세요.";
        }
        String message = error.getMessage();
        return message == null || message.trim().isEmpty()
                ? "이 파일을 처리할 수 없습니다. 지원하는 사진인지 확인한 후 다시 시도해 주세요."
                : message;
    }

    private String exportName() {
        String name = sourceName.replaceAll("[\\\\/:*?\"<>|]", "_");
        int dot = name.lastIndexOf('.');
        if (dot > 0) name = name.substring(0, dot);
        if (name.length() > 70) name = name.substring(0, 70);
        return name + "_film-lab.jpg";
    }

    private void addSlider(LinearLayout parent, String key, String title, int min, int max) {
        Slider slider = new Slider(key, title, min, max);
        sliders.add(slider);
        add(parent, slider.root, 0, 6);
    }

    private final class Slider {
        final String key;
        final String title;
        final int min;
        final LinearLayout root;
        final SeekBar seek;
        final TextView value;
        FilmEngine.Settings gestureStart;

        Slider(String key, String title, int min, int max) {
            this.key = key;
            this.title = title;
            this.min = min;
            root = column();
            LinearLayout label = row();
            TextView name = text(title, 13, WHITE);
            label.addView(name, new LinearLayout.LayoutParams(0, -2, 1));
            value = text("", 13, AMBER);
            value.setGravity(Gravity.END);
            value.setMinWidth(dp(55));
            label.addView(value);
            root.addView(label);
            seek = new SeekBar(MainActivity.this);
            seek.setMax(max - min);
            seek.setProgressTintList(ColorStateList.valueOf(AMBER));
            seek.setThumbTintList(ColorStateList.valueOf(AMBER));
            seek.setProgressBackgroundTintList(ColorStateList.valueOf(BORDER));
            seek.setPadding(dp(8), 0, dp(8), 0);
            root.addView(seek, new LinearLayout.LayoutParams(-1, dp(48)));
            seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
                @Override public void onStartTrackingTouch(SeekBar bar) {
                    gestureStart = settings.copy();
                }

                @Override public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                    if (updatingWidgets || !fromUser) return;
                    int next = progress + min;
                    if (next == sliderValue(key)) return;
                    if (gestureStart == null) pushHistory(settings);
                    setSliderValue(key, next);
                    updateValue();
                    requestPreview();
                }

                @Override public void onStopTrackingTouch(SeekBar bar) {
                    if (gestureStart != null && !sameSettings(gestureStart, settings)) {
                        pushHistory(gestureStart);
                    }
                    gestureStart = null;
                    updateEnabled();
                }
            });
        }

        void updateValue() {
            int number = sliderValue(key);
            String display = "exposure".equals(key)
                    ? String.format(Locale.US, "%+.1f EV", number / 10f)
                    : (("grain".equals(key) || "strength".equals(key)) ? number + "%"
                    : (number > 0 ? "+" : "") + number);
            value.setText(display);
            seek.setContentDescription(title + ": " + display);
        }
    }

    private int sliderValue(String key) {
        switch (key) {
            case "strength": return Math.round(settings.strength * 100);
            case "exposure": return Math.round(settings.exposure * 10);
            case "warmth": return settings.warmth;
            case "tint": return settings.tint;
            case "contrast": return settings.contrast;
            case "saturation": return settings.saturation;
            case "highlights": return settings.highlights;
            case "shadows": return settings.shadows;
            case "grain": return settings.grain;
            default: return 0;
        }
    }

    private void setSliderValue(String key, int value) {
        switch (key) {
            case "strength": settings.strength = value / 100f; break;
            case "exposure": settings.exposure = value / 10f; break;
            case "warmth": settings.warmth = value; break;
            case "tint": settings.tint = value; break;
            case "contrast": settings.contrast = value; break;
            case "saturation": settings.saturation = value; break;
            case "highlights": settings.highlights = value; break;
            case "shadows": settings.shadows = value; break;
            case "grain": settings.grain = value; break;
        }
    }

    private void syncWidgets() {
        updatingWidgets = true;
        for (Slider slider : sliders) {
            slider.seek.setProgress(sliderValue(slider.key) - slider.min);
            slider.updateValue();
        }
        for (int i = 0; i < styleButtons.size(); i++) {
            boolean selected = STYLE_IDS[i].equals(settings.styleId);
            Button button = styleButtons.get(i);
            button.setSelected(selected);
            button.setTextColor(selected ? BACKGROUND : WHITE);
            button.setBackgroundTintList(null);
            button.setBackground(shape(selected ? AMBER : PANEL, 11, selected ? AMBER : BORDER));
            button.setContentDescription(STYLE_NAMES[i] + (selected ? ", 선택됨" : ""));
            if (selected) styleNote.setText(STYLE_NOTES[i]);
        }
        updatingWidgets = false;
        updateEnabled();
    }

    private void pushHistory(FilmEngine.Settings previous) {
        if (history.size() >= 30) history.removeFirst();
        history.addLast(previous.copy());
        updateEnabled();
    }

    private static boolean sameSettings(FilmEngine.Settings a, FilmEngine.Settings b) {
        return a.styleId.equals(b.styleId) && a.exposure == b.exposure && a.strength == b.strength
                && a.warmth == b.warmth && a.tint == b.tint && a.contrast == b.contrast
                && a.saturation == b.saturation && a.highlights == b.highlights
                && a.shadows == b.shadows && a.grain == b.grain;
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putBundle("settings", writeSettings(settings));
        out.putString("sourceName", sourceName);
        Uri restoreSource = loadingSourceUri != null ? loadingSourceUri : sourceUri;
        if (restoreSource != null) out.putString("sourceUri", restoreSource.toString());
        out.putBoolean("exportPickerOpen", exportPickerOpen);
        if (exportSource != null) out.putString("exportSource", exportSource.toString());
        if (exportSettings != null) out.putBundle("exportSettings", writeSettings(exportSettings));
        out.putInt("exportEdge", exportEdge);
    }

    private static Bundle writeSettings(FilmEngine.Settings values) {
        Bundle out = new Bundle();
        out.putString("styleId", values.styleId);
        out.putFloat("exposure", values.exposure);
        out.putFloat("strength", values.strength);
        out.putInt("warmth", values.warmth);
        out.putInt("tint", values.tint);
        out.putInt("contrast", values.contrast);
        out.putInt("saturation", values.saturation);
        out.putInt("highlights", values.highlights);
        out.putInt("shadows", values.shadows);
        out.putInt("grain", values.grain);
        return out;
    }

    private static FilmEngine.Settings readSettings(Bundle state) {
        FilmEngine.Settings values = new FilmEngine.Settings();
        if (state == null) return values;
        String style = state.getString("styleId", "baseline");
        for (String known : STYLE_IDS) if (known.equals(style)) values.styleId = style;
        values.exposure = Math.max(-2, Math.min(3, state.getFloat("exposure", 0)));
        values.strength = Math.max(0, Math.min(1, state.getFloat("strength", 1)));
        values.warmth = clamp(state.getInt("warmth", 0), -100, 100);
        values.tint = clamp(state.getInt("tint", 0), -100, 100);
        values.contrast = clamp(state.getInt("contrast", 0), -100, 100);
        values.saturation = clamp(state.getInt("saturation", 0), -100, 100);
        values.highlights = clamp(state.getInt("highlights", 0), -100, 100);
        values.shadows = clamp(state.getInt("shadows", 0), -100, 100);
        values.grain = clamp(state.getInt("grain", 0), 0, 100);
        return values;
    }

    @Override protected void onPause() {
        showOriginal(false);
        super.onPause();
    }

    @Override public void onBackPressed() {
        handleBack();
    }

    private void handleBack() {
        if (exporting) {
            Toast.makeText(this, "JPEG 저장을 마친 뒤 닫아 주세요", Toast.LENGTH_SHORT).show();
        } else {
            finish();
        }
    }

    @Override protected void onDestroy() {
        destroyed = true;
        generation.incrementAndGet();
        loadGeneration.incrementAndGet();
        cancelPendingPreview();
        worker.shutdown();
        super.onDestroy();
    }

    private void cancelPendingPreview() {
        if (pendingPreview != null) {
            main.removeCallbacks(pendingPreview);
            pendingPreview = null;
        }
    }

    private LinearLayout sectionTitle(String number, String title) {
        LinearLayout line = row();
        TextView index = text(number, 11, AMBER);
        index.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        line.addView(index);
        TextView name = text(title, 18, WHITE);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
        params.setMargins(dp(10), 0, 0, 0);
        line.addView(name, params);
        return line;
    }

    private LinearLayout row() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.HORIZONTAL);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    private LinearLayout column() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        return view;
    }

    private TextView text(String value, float size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setFontFeatureSettings("tnum");
        return view;
    }

    private Button button(String value, boolean primary) {
        Button view = new Button(this);
        view.setText(value);
        view.setTextSize(14);
        view.setAllCaps(false);
        view.setTextColor(primary ? BACKGROUND : WHITE);
        view.setMinHeight(dp(49));
        view.setMinimumHeight(dp(49));
        view.setMinWidth(0);
        view.setMinimumWidth(0);
        view.setPadding(dp(13), dp(10), dp(13), dp(10));
        view.setStateListAnimator(null);
        view.setBackgroundTintList(null);
        view.setBackground(shape(primary ? AMBER : PANEL, 11, primary ? AMBER : BORDER));
        return view;
    }

    private GradientDrawable shape(int color, int radius, int stroke) {
        GradientDrawable result = new GradientDrawable();
        result.setColor(color);
        result.setCornerRadius(dp(radius));
        result.setStroke(dp(1), stroke);
        return result;
    }

    private void add(LinearLayout parent, View child, int horizontalMargin, int topMargin) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(dp(horizontalMargin), dp(topMargin), dp(horizontalMargin), 0);
        parent.addView(child, params);
    }

    private LinearLayout.LayoutParams weighted(int left, int right) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
        params.setMargins(dp(left), 0, dp(right), 0);
        return params;
    }

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
