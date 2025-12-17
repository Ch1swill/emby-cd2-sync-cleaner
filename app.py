import os
import json
import logging
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

    # 2. 检查 .strm 后缀
    if not emby_path.lower().endswith('.strm'):
        logging.info(f"🚫 忽略非 strm 文件/目录: {emby_path}")
        return jsonify({"status": "ignored_not_strm"}), 200

    file_name_full = os.path.basename(emby_path)
    base_name = os.path.splitext(file_name_full)[0]
    
    logging.info(f"🎯 锁定目标: {base_name} (原路径: {emby_path})")

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
            if file.startswith(base_name):
                fname_no_ext = os.path.splitext(file)[0]
                if fname_no_ext == base_name or file.startswith(base_name + "."):
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
        logging.warning(f"⚠️ 未找到名为 {base_name} 的文件。")
        return jsonify({"status": "not_found"}), 200

if __name__ == '__main__':
    run_port = int(os.environ.get('APP_PORT', DEFAULT_PORT))
    logging.info(f"🚀 服务已启动，监听端口: {run_port}")
    serve(app, host='0.0.0.0', port=run_port)