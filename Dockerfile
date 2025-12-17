FROM python:3.9-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY app.py .
# 复制默认配置（防止用户没挂载时报错，虽然会被覆盖）
COPY config.json .

# 暴露端口
EXPOSE 5005

# 启动
CMD ["python", "app.py"]