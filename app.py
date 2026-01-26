import os
import sys
import json
import logging
import threading
import time
import re
from flask import Flask, request, jsonify
from waitress import serve

# ================= 配置加载 =================
CONFIG_FILE = 'config.json'
DEFAULT_PORT = 5005

# 初始化日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"❌ 找不到配置文件: {CONFIG_FILE}")
        return {}, 4
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config.get('path_mapping', {}), config.get('min_filename_length', 4)
    except Exception as e:
        logging.error(f"❌ 配置文件读取失败: {e}")
        return {}, 4

PATH_MAPPING, MIN_FILENAME_LENGTH = load_config()

def extract_media_title(filename):
    """
    从文件名中提取媒体标题
    支持格式:
    - 电影: "疯狂动物城2 (2025).1080p.x264" -> "疯狂动物城2 (2025)"
    - 电视剧: "白莲花度假村 S01E05 2160p.CHDWEB" -> "白莲花度假村 S01E05"
    - 电视剧: "白莲花度假村 S01E05.2160p.CHDWEB" -> "白莲花度假村 S01E05"
    如果无法提取，返回 None
    """
    # 优先匹配电视剧格式: "标题 S01E05" 或 "标题 S01E05."
    # 支持 S01E05, S1E5, S01E05E06 (多集) 等格式
    tv_match = re.match(r'^(.+?[\s\.]+S\d{1,2}E\d{1,2}(?:E\d{1,2})?)', filename, re.IGNORECASE)
    if tv_match:
        return tv_match.group(1).rstrip('.')

    # 匹配电影格式: "标题 (年份)"
    movie_match = re.match(r'^(.+?\s*\(\d{4}\))', filename)
    if movie_match:
        return movie_match.group(1)

    return None

# ================= 挂载检测 =================
MOUNT_CHECK_INTERVAL = int(os.environ.get('MOUNT_CHECK_INTERVAL', 30))  # 检测间隔(秒)
MOUNT_CHECK_ENABLED = os.environ.get('MOUNT_CHECK_ENABLED', 'true').lower() == 'true'

def get_mount_paths():
    """从配置中提取所有需要监控的挂载路径"""
    paths = []
    for config_value in PATH_MAPPING.values():
        if isinstance(config_value, dict):
            path = config_value.get('local_path', '')
        else:
            path = str(config_value)
        if path:
            paths.append(path)
    return paths

def check_mounts():
    """检查所有挂载点是否可用"""
    mount_paths = get_mount_paths()
    if not mount_paths:
        return True

    for path in mount_paths:
        if not os.path.exists(path):
            return False
        # 尝试列出目录内容，确认挂载真正可用
        try:
            os.listdir(path)
        except OSError:
            return False
    return True

def mount_monitor():
    """后台线程：定期检测挂载状态"""
    logging.info(f"🔍 挂载监控已启动，检测间隔: {MOUNT_CHECK_INTERVAL}秒")

    while True:
        time.sleep(MOUNT_CHECK_INTERVAL)
        if not check_mounts():
            logging.error("❌ 检测到CD2挂载丢失，容器将自动重启...")
            time.sleep(2)  # 等待日志写入
            os._exit(1)  # 强制退出，触发Docker重启

def start_mount_monitor():
    """启动挂载监控线程"""
    if not MOUNT_CHECK_ENABLED:
        logging.info("⏸️ 挂载监控已禁用 (MOUNT_CHECK_ENABLED=false)")
        return

    if not get_mount_paths():
        logging.warning("⚠️ 未配置挂载路径，跳过挂载监控")
        return

    monitor_thread = threading.Thread(target=mount_monitor, daemon=True)
    monitor_thread.start()
# ===========================================

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def emby_webhook():
    logging.info("⚡ 收到 Webhook 请求")
    
    # 1. 解析数据
    data = None
    try:
        if request.is_json:
            data = request.json
        elif request.form.get('data'):
            data = json.loads(request.form.get('data'))
        elif request.values.get('data'):
            data = json.loads(request.values.get('data'))
    except Exception as e:
        logging.error(f"解析失败: {e}")
        return jsonify({"status": "error"}), 400

    if not data:
        return jsonify({"status": "no_data"}), 400

    event = data.get('Event', '')
    if event not in ['library.deleted', 'item.deleted']:
        return jsonify({"status": "ignored"}), 200

    item = data.get('Item', {})
    emby_path = item.get('Path', '')
    
    if not emby_path:
        return jsonify({"status": "no_path"}), 200

    # 2. 判断是文件还是目录删除
    is_directory_delete = not emby_path.lower().endswith('.strm')

    if is_directory_delete:
        # ========== 目录删除模式 ==========
        # 先检查是否有匹配的映射且 clean_dirs=true
        sorted_mappings = sorted(PATH_MAPPING.items(), key=lambda x: len(x[0]), reverse=True)
        enable_clean_dirs = False
        cloud_root = None

        for emby_root, config_value in sorted_mappings:
            if emby_path.startswith(emby_root):
                if isinstance(config_value, dict):
                    enable_clean_dirs = config_value.get('clean_dirs', True)
                    cloud_root = config_value.get('local_path', '')
                else:
                    enable_clean_dirs = True
                    cloud_root = str(config_value)
                break

        # 如果 clean_dirs=false，按原逻辑忽略目录删除
        if not enable_clean_dirs:
            logging.info(f"🚫 忽略非 strm 文件/目录: {emby_path}")
            return jsonify({"status": "ignored_not_strm"}), 200

        dir_name = os.path.basename(emby_path.rstrip('/\\'))

        # 安全检查：目录名长度
        if len(dir_name) < MIN_FILENAME_LENGTH:
            logging.warning(f"🛑 目录名过短 [{dir_name}]，停止操作。")
            return jsonify({"status": "safety_block"}), 200

        logging.info(f"📁 检测到目录删除: {emby_path}")

        if not cloud_root:
            logging.warning("⚠️ 未配置监控目录，跳过。")
            return jsonify({"status": "path_not_mapped"}), 200

        # 计算云存储对应路径
        for emby_root, _ in sorted_mappings:
            if emby_path.startswith(emby_root):
                relative_path = emby_path.replace(emby_root, "", 1)
                if relative_path.startswith('/') or relative_path.startswith('\\'):
                    relative_path = relative_path[1:]
                target_cloud_dir = os.path.join(cloud_root, relative_path)
                break

        if not os.path.exists(target_cloud_dir):
            logging.warning(f"⚠️ 云存储目录不存在: {target_cloud_dir}")
            return jsonify({"status": "cloud_dir_not_found"}), 200

        if not os.path.isdir(target_cloud_dir):
            logging.warning(f"⚠️ 目标路径不是目录: {target_cloud_dir}")
            return jsonify({"status": "not_a_directory"}), 200

        # 执行目录删除
        logging.info(f"🗑️ 准备删除云存储目录: {target_cloud_dir}")

        deleted_files = 0
        deleted_dirs = 0

        # 从底部向上删除
        for root, dirs, files in os.walk(target_cloud_dir, topdown=False):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    logging.info(f"🔪 [文件] 已删除: {file_path}")
                    deleted_files += 1
                except Exception as e:
                    logging.error(f"❌ 删除文件失败: {file_path} - {e}")

            # 删除空目录
            try:
                os.rmdir(root)
                logging.info(f"🧹 [目录] 已删除: {root}")
                deleted_dirs += 1
            except Exception as e:
                logging.error(f"❌ 删除目录失败: {root} - {e}")

        logging.info(f"✅ 目录删除完成: 删除 {deleted_files} 个文件, {deleted_dirs} 个目录")
        return jsonify({"status": "success", "deleted_files": deleted_files, "deleted_dirs": deleted_dirs}), 200

    # ========== 单文件删除模式 (.strm) ==========
    file_name_full = os.path.basename(emby_path)
    base_name = os.path.splitext(file_name_full)[0]

    # 尝试提取媒体标题（如 "疯狂动物城2 (2025)"）
    media_title = extract_media_title(base_name)
    if media_title:
        logging.info(f"🎯 锁定目标: {base_name} (原路径: {emby_path})")
        logging.info(f"📽️ 识别媒体标题: {media_title} (将匹配所有相关文件)")
        match_prefix = media_title
    else:
        logging.info(f"🎯 锁定目标: {base_name} (原路径: {emby_path})")
        logging.info(f"⚠️ 无法提取媒体标题，使用完整文件名匹配")
        match_prefix = base_name

    if len(base_name) < MIN_FILENAME_LENGTH:
        logging.warning(f"🛑 文件名过短，停止操作。")
        return jsonify({"status": "safety_block"}), 200

    # 3. 智能路径计算
    target_search_dir = None
    enable_clean_dirs = True # 默认开启清理
    
    # 排序：优先匹配长路径
    sorted_mappings = sorted(PATH_MAPPING.items(), key=lambda x: len(x[0]), reverse=True)

    for emby_root, config_value in sorted_mappings:
        if emby_path.startswith(emby_root):
            
            # === 解析配置 (支持字符串或对象) ===
            cloud_root = ""
            if isinstance(config_value, dict):
                # 如果是对象写法: {"local_path": "...", "clean_dirs": false}
                cloud_root = config_value.get('local_path', '')
                enable_clean_dirs = config_value.get('clean_dirs', True)
            else:
                # 如果是简单字符串写法: "/mnt/..."
                cloud_root = str(config_value)
                enable_clean_dirs = True
            
            if not cloud_root:
                continue

            # === 计算路径 ===
            relative_full_path = emby_path.replace(emby_root, "", 1)
            relative_dir = os.path.dirname(relative_full_path)
            if relative_dir.startswith('/') or relative_dir.startswith('\\'):
                relative_dir = relative_dir[1:]
                
            precise_dir = os.path.join(cloud_root, relative_dir)
            
            if os.path.exists(precise_dir):
                target_search_dir = precise_dir
                logging.info(f"🚀 智能导航成功: 直接空降至 [{target_search_dir}]")
                logging.info(f"⚙️ 当前规则清理策略: {'[开启] 清理空目录' if enable_clean_dirs else '[关闭] 保留空目录'}")
            else:
                logging.warning(f"⚠️ 精准目录 [{precise_dir}] 不存在，降级为根目录全盘搜索")
                target_search_dir = cloud_root
            break
            
    if not target_search_dir:
        logging.warning("⚠️ 未配置监控目录，跳过。")
        return jsonify({"status": "path_not_mapped"}), 200

    if not os.path.exists(target_search_dir):
        logging.warning(f"⚠️ 最终搜索目录不存在: {target_search_dir}")
        return jsonify({"status": "dir_not_found"}), 200

    # 4. 执行搜索与删除
    logging.info(f"🕵️ 开始搜索...")
    
    deleted_count = 0
    dirs_to_clean = set()

    for root, dirs, files in os.walk(target_search_dir, topdown=False):
        for file in files:
            if file.startswith(match_prefix):
                fname_no_ext = os.path.splitext(file)[0]
                if fname_no_ext == match_prefix or file.startswith(match_prefix + "."):
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                        logging.info(f"🔪 [文件] 已删除: {file_path}")
                        deleted_count += 1
                        dirs_to_clean.add(root)
                    except Exception as e:
                        logging.error(f"❌ 删除失败: {e}")

        # === 5. 目录清理 (根据开关决定是否执行) ===
        if enable_clean_dirs:
            if root in dirs_to_clean:
                if not os.listdir(root):
                    try:
                        os.rmdir(root)
                        logging.info(f"🧹 [目录] 文件夹已空，移除: {root}")
                    except:
                        pass
        # 如果 enable_clean_dirs 为 False，则跳过上面这段逻辑，保留文件夹

    if deleted_count > 0:
        return jsonify({"status": "success", "deleted": deleted_count}), 200
    else:
        logging.warning(f"⚠️ 未找到匹配 [{match_prefix}] 的文件。")
        return jsonify({"status": "not_found"}), 200

if __name__ == '__main__':
    run_port = int(os.environ.get('APP_PORT', DEFAULT_PORT))
    start_mount_monitor()  # 启动挂载监控
    logging.info(f"🚀 服务已启动，监听端口: {run_port}")
    serve(app, host='0.0.0.0', port=run_port)