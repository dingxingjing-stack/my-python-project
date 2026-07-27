# Cloudflare Pages 部署步骤

## 前置条件
- GitHub 仓库已包含 `ai-music-beta/` 目录（完整前端的 Vue3 源码）
- Cloudflare 账号

## 部署步骤

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Workers & Pages**

2. 点击 **Create** → **Pages** → **Connect to Git**

3. 授权 GitHub → 选择 `dingxingjing-stack/my-python-project` 仓库

4. 配置构建参数：
   - **项目名称**: `avireon-beta`（或自定义）
   - **生产分支**: `main`
   - **构建目录**: `ai-music-beta`（重要！项目在子目录中）
   - **构建命令**: `npm run build`
   - **构建输出目录**: `dist`

5. **配置环境变量**（选做）：
   在 Cloudflare Pages 项目 → **Settings** → **Environment variables** → 添加：
   - `VITE_API_BASE` = `https://ai-music-backend-db6h.onrender.com`
   - 如不需改动则可跳过，代码已内置默认值

6. 点击 **Save and Deploy**

7. 等待部署完成（约 1-2 分钟），Cloudflare 会自动分配域名如 `avireon-beta.pages.dev`

## 自定义域名（可选）

在 Pages 项目 → **Custom domains** → **Set up a custom domain** → 输入域名，按提示配置 DNS

## 更新站点

每次推送 `main` 分支的代码到 GitHub，Cloudflare Pages 会自动触发重新构建部署。
