# Emby-CD2-Sync-Cleaner

**专为 "Emby + CloudDrive2 + 115网盘 (.strm)" 架构设计的自动删档工具。**

## 痛点与解决方案 🎯

在使用 **CloudDrive2 (CD2)** 挂载网盘并配合 **Emby** 刮削 `.strm` 文件时，我们经常遇到一个尴尬的问题：

* **痛点**：当你在 Emby 客户端点击“删除”一部烂片时，Emby 只会删除本地那个几 KB 大小的 `.strm` 索引文件。**网盘里那个几十 GB 的原始视频文件依然存在**，占用着宝贵的空间。
* **解决方案**：这个 Docker 容器作为一个 Webhook 服务运行。当 Emby 删除媒体时，它会通过 Webhook 接收通知，计算出网盘挂载点的真实路径，并**精准猎杀**对应的源文件，同时支持自动清理空文件夹。

## 核心特性 ✨

* **⚡ 毫秒级响应**：基于 Webhook 触发，而非定时轮询，删除操作实时生效。
* **🧠 智能导航 (Smart Navigator)**：不需要全盘扫描。脚本会自动根据 strm 路径计算出网盘内的相对路径，“空降”到目标目录进行删除，效率极高。（请保证本地和115目录树结构一致）
* **🛡️ Strm 专属猎手**：内置安全机制，**仅响应 `.strm` 文件的删除事件**。误删本地实体 MP4 或文件夹不会触发网盘删除，确保安全。
* **🧹 智能扫尾**：支持“删文件后自动删除空文件夹”。该功能可针对不同目录单独开启或关闭（例如：国产电影库删空目录，欧美电影库保留目录结构）。
* **🐳 Docker 部署**：轻量级容器，配置简单，支持群晖、Unraid、绿联等各种 NAS 环境。

## 运行原理 ⚙️

1. **用户操作**：在 Emby 客户端点击“删除”。
2. **Emby**：删除本地 `.strm` 文件，并向本服务发送 Webhook 通知。
3. **Cleaner**：
* 解析通知，确认是 `.strm` 文件。
* 根据 `config.json` 的映射规则，将 Emby 路径转换为容器内的挂载路径。
* 定位到网盘挂载目录下的同名视频文件（如 `abc.mkv`）。


4. **执行**：删除视频源文件 -> (可选) 如果文件夹空了，顺手删除文件夹。
5. **CD2**：检测到本地挂载文件消失，同步删除网盘云端文件（通常进入回收站）。

## 快速开始 🚀

### 1. 目录结构

在你的 NAS 或服务器上创建一个文件夹，放入以下文件：

* `docker-compose.yaml`
* `config.json`

### 2. 配置 docker-compose.yaml

**⚠️ 关键：理解路径映射**
你需要将宿主机上 **CloudDrive2 的挂载目录** 映射进容器，否则脚本无法删除文件。

```yaml
services:
  emby-cd2-cleaner:
    container_name: emby-cd2-cleaner
    image: ch1swill/emby-cd2-sync-cleaner:latest
    restart: unless-stopped
    ports:
      - "5005:5005"
    volumes:
      # 1. 挂载配置文件
      - ./config.json:/app/config.json
      
      # 2. 挂载网盘目录 (最关键的一步！)
      # 格式: - 宿主机CD2挂载路径 : 容器内路径
      # 建议保持两边一致，避免混乱
      - /volume1/docker/CloudNAS:/volume1/docker/CloudNAS 
    environment:
      - TZ=Asia/Shanghai
      - PYTHONUNBUFFERED=1

```

### 3. 配置 config.json

此文件告诉脚本：**Emby 里的路径** 对应 **容器里的哪个路径**。
举例：你的Emby中映射关系为/volume1/media:/mnt/share

```json
{
  "min_filename_length": 4,
  "path_mapping": {
    "__说明__": "左边是Emby显示的路径，右边是容器内看到的路径",
    
    "/mnt/share/电影": {
      "local_path": "/volume1/docker/CloudNAS/115/电影",
      "clean_dirs": true
    },
    
    "/mnt/share/电视剧": {
      "local_path": "/volume1/docker/CloudNAS/115/电视剧",
      "clean_dirs": false
    }
  }
}

```

* `local_path`: **必须**与 `docker-compose.yml` 中映射到容器内的路径一致。
* `clean_dirs`: `true` 表示删完视频如果文件夹空了，就删除文件夹；`false` 表示保留空文件夹。

### 4. 启动服务

```bash
docker-compose up -d

```

### 5. 配置 Emby Webhook

1. 进入 Emby 控制台 -> **Webhooks**（需要 Emby Premiere）。
2. 点击 **添加 Webhook**。
3. **Url**: `http://你的NAS_IP:5005/webhook`
4. **数据类型**: `application/json`
5. **事件**: 勾选 `媒体库 (Library)` -> `已删除的项目 (Item Deleted)`。
6. 点击保存。

## 常见问题 (FAQ) ❓

**Q: 我在 Emby 删除了一个普通的 MP4 文件，网盘的会被删吗？**
A: **不会。** 脚本有严格的后缀名检查，只有当 Emby 删除的文件后缀是 `.strm` 时，脚本才会启动搜索和删除逻辑。

**Q: `docker-compose` 里的路径到底怎么填？**
A: 请遵循 **"三方一致原则"**：

1. **宿主机**：`/host/path` (文件真正所在位置)
2. **Compose 映射**：`- /host/path:/container/path`
3. **Config 配置**：`"local_path": "/container/path"`
*简单做法：把容器内路径设置得和宿主机路径一模一样。*

**Q: 删了的文件能找回吗？**
A: 这取决于 CloudDrive2 和 115 网盘的机制。通常情况下，通过 CD2 删除的文件会进入 115 网盘的 **“回收站”**，你可以去那里找回。但请务必先用测试文件进行测试！

## 免责声明 ⚠️

本工具涉及文件删除操作。虽然代码中包含了多重安全检查（如文件名长度限制、后缀名检查、非递归删除等），但作者不对因配置错误或程序 Bug 导致的任何数据丢失负责。

**请务必先使用不重要的测试文件验证配置正确后，再投入生产环境使用。**

---

*Made with ❤️ for the Emby & 115 Community.*