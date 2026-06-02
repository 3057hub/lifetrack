#!/bin/bash
set -e
echo "===== LifeTrack 部署脚本 ====="

# 1. 检测系统并安装依赖
if command -v apt &>/dev/null; then
  echo "[1/6] apt 安装 nginx git python3-pip..."
  apt update -qq && apt install -y -qq nginx git python3-pip
elif command -v yum &>/dev/null; then
  echo "[1/6] yum 安装 nginx git python3-pip..."
  yum install -y nginx git python3-pip
else
  echo "未知系统，请手动安装: nginx git python3-pip"
  exit 1
fi

# 2. 克隆代码
echo "[2/6] 克隆代码..."
rm -rf /opt/lifetrack
git clone https://github.com/3057hub/lifetrack.git /opt/lifetrack
cd /opt/lifetrack
pip3 install -r requirements.txt -q

# 3. 配置环境变量
echo "[3/6] 配置环境变量..."
cat > /opt/lifetrack/.env << 'ENVEOF'
DEEPSEEK_API_KEY=sk-b7047c0cd1f34ffa9e8afdc6f58a7f33
APP_PASSWORD=yzh2026
ENVEOF

# 4. 创建 systemd 服务
echo "[4/6] 配置 systemd..."
cat > /etc/systemd/system/lifetrack.service << 'UNITEOF'
[Unit]
Description=LifeTrack API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/lifetrack
EnvironmentFile=/opt/lifetrack/.env
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable lifetrack
systemctl restart lifetrack

# 5. 配置 Nginx
echo "[5/6] 配置 Nginx..."
cat > /etc/nginx/conf.d/lifetrack.conf << 'NGXEOF'
server {
    listen 80;
    server_name _;

    root /opt/lifetrack/static;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGXEOF

# 确保 nginx 主配置加载 conf.d
grep -q "conf.d" /etc/nginx/nginx.conf || echo "检查 nginx.conf 是否包含 conf.d"

# 启动 nginx
systemctl enable nginx
systemctl restart nginx || nginx -t && nginx

# 6. 检查状态
echo "[6/6] 验证..."
sleep 2
echo ""
echo "--- uvicorn 状态 ---"
systemctl status lifetrack --no-pager -l | head -10
echo ""
echo "--- nginx 状态 ---"
systemctl status nginx --no-pager -l | head -5
echo ""
echo "--- 测试 API ---"
curl -s http://127.0.0.1:8000/api/auth/verify -X POST -H "Content-Type: application/json" -d '{"password":"yzh2026"}'
echo ""
echo "--- 测试前端 ---"
curl -s http://127.0.0.1/ | grep -c "LifeTrack" | xargs echo "LifeTrack 出现次数:"
echo ""
echo "===== 部署完成 ====="
echo "访问地址: http://114.55.99.78"
echo "密码: yzh2026"
