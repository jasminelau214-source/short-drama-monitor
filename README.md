# 短剧热播榜研究工具 Python 本地版

## 运行

Windows 双击 `启动短剧研究工具.bat`，或在当前文件夹运行：

```text
python app.py
```

浏览器会自动打开 `http://127.0.0.1:4173/`。

## 编辑与保存

1. 在榜单中点击剧名打开详情。
2. 点击“编辑资料”。
3. 填写后点击“保存资料”。
4. 修改内容保存在同目录的 `short_drama.sqlite3`，刷新网页不会丢失。

## 分享

- 直接发送 `短剧热播榜研究工具_离线版.html`，接收者可在浏览器打开。
- 离线HTML里的修改只保存在各自浏览器，可通过“导出填写结果”备份。
- Python版默认只在本机访问；同一局域网共享时运行 `python app.py --host 0.0.0.0`，并使用本机局域网IP访问。

## 文件

- `app.py`：Python后端、数据接口、SQLite保存逻辑。
- `index.html`：公开研究前端。
- `review.html`：可编辑资料表单。
- `data.json`：两天真实榜单与已有剧目资料。
- `短剧热播榜研究工具_离线版.html`：无需Python即可打开的单文件版本。

## Render公网部署

1. 把本文件夹上传到自己的 GitHub 仓库。
2. 在 Render 选择 New > Blueprint，并连接该仓库。
3. 首次部署时为 `ADMIN_PASSWORD` 设置一个强密码。
4. 部署完成后会获得公开的 `https://项目名.onrender.com` 地址。

公开首页无需登录；进入 `/review` 时使用用户名 `admin` 和你设置的密码。

免费 Render Web Service 的 SQLite 文件不是永久存储，休眠、重启或重新部署后填写结果可能丢失。正式使用时请添加持久化磁盘并设置 `DATA_DIR=/var/data`，或改接 Postgres。
