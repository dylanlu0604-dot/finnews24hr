# 新浪財經 7×24 快訊聚合器

自動爬取新浪財經 7×24 快訊，轉換為繁體中文，每 5 分鐘透過 GitHub Actions 更新，並透過 GitHub Pages 展示。

## 專案結構

```
├── scraper/
│   └── fetch_sina.py       # 爬蟲主程式（簡體→繁體）
├── docs/
│   ├── index.html          # GitHub Pages 前端
│   └── data.json           # 爬蟲產出（自動更新）
├── .github/workflows/
│   └── scrape.yml          # 自動排程（每 5 分鐘）
└── requirements.txt
```

## 快速部署步驟

### 1. 建立 GitHub Repository

1. 前往 [github.com/new](https://github.com/new)
2. Repository name：`sina-7x24`（或自訂）
3. 設為 **Public**（GitHub Pages 免費方案需要）
4. 不要勾選任何初始化選項，直接建立

### 2. 上傳本專案

```bash
# 在本機專案資料夾執行
git init
git add .
git commit -m "init: sina 7x24 scraper"
git branch -M main
git remote add origin https://github.com/你的帳號/sina-7x24.git
git push -u origin main
```

### 3. 啟用 GitHub Pages

1. 進入 repo → **Settings** → **Pages**
2. Source：**Deploy from a branch**
3. Branch：`main`，Folder：`/docs`
4. 儲存 → 幾分鐘後網頁網址會出現（格式：`https://你的帳號.github.io/sina-7x24/`）

### 4. 確認 Actions 權限

1. 進入 repo → **Settings** → **Actions** → **General**
2. **Workflow permissions** 選 `Read and write permissions`
3. 儲存

### 5. 手動觸發第一次爬取

1. 進入 repo → **Actions** → **Sina 7x24 Scraper**
2. 點選 **Run workflow** → 確認

之後每 5 分鐘會自動執行。

---

## 本機測試

```bash
pip install -r requirements.txt
python scraper/fetch_sina.py
# 產生 docs/data.json 後，用瀏覽器開啟 docs/index.html
```

> 注意：直接開啟 HTML 檔案因 CORS 限制無法讀取 data.json，建議用 `python -m http.server` 在 `docs/` 目錄下啟動本機伺服器。

---

## 功能說明

| 功能 | 說明 |
|------|------|
| 自動更新 | GitHub Actions 每 5 分鐘爬取，網頁每 5 分鐘自動重載 |
| 繁體轉換 | `opencc-python-reimplemented` s2twp 模式（臺灣正體） |
| 時段篩選 | 1H / 6H / 24H / ALL |
| 標籤篩選 | 點選任一標籤過濾，再次點選取消 |
| 關鍵字搜尋 | 即時過濾，符合文字以橘色標亮 |
| 最後更新時間 | 頂欄即時顯示（+08:00 台灣時間） |
