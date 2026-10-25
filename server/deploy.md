# 共享题库服务部署

目标机：<你的服务器IP>（阿里云香港 ECS，Ubuntu 22.04）
代码位置：`/opt/apps/chaoxing-lib/`
监听：`127.0.0.1:5001`（只本机，由 nginx 反代对外）

## 一、准备目录与依赖

```bash
mkdir -p /opt/apps/chaoxing-lib
cd /opt/apps/chaoxing-lib
# 上传 library_service.py 到本目录

apt-get install -y python3-venv         # 若已存在可跳过
python3 -m venv .venv
.venv/bin/pip install flask gunicorn
```

## 二、令牌与数据目录

```bash
# 生成访问令牌（客户端要填这个）
python3 -c "import secrets; print(secrets.token_urlsafe(24))"

# 数据放在 /opt/apps 下，不要放系统目录
mkdir -p /opt/apps/chaoxing-lib/data
chmod 750 /opt/apps/chaoxing-lib/data
```

写入环境文件：

```bash
cat > /opt/apps/chaoxing-lib/env <<'EOF'
LIB_DB=/opt/apps/chaoxing-lib/data/shared.db
LIB_TOKEN=把上面生成的令牌粘进来
LIB_PORT=5001
LIB_DAILY_LIMIT=2000
EOF
chmod 600 /opt/apps/chaoxing-lib/env
```

## 三、systemd 单元

```bash
cp chaoxing-lib.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chaoxing-lib
systemctl status chaoxing-lib --no-pager
```

## 四、nginx 反代

在 `lingluandf.online` 的 server 块里加（**不要动博客站点原有的 location**）：

```nginx
location /lib/ {
    proxy_pass http://127.0.0.1:5001/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 15s;
}
```

然后 `nginx -t && systemctl reload nginx`。

客户端填写的服务地址就是 `https://lingluandf.online/lib`。

## 五、验证

```bash
curl -s http://127.0.0.1:5001/healthz
curl -s http://127.0.0.1:5001/api/v1/stats
# 外网验证（需要域名或安全组放行）
curl -s https://lingluandf.online/lib/healthz
```

## 六、备份

```bash
# 每天一次，保留 7 天
cat > /etc/cron.daily/chaoxing-lib-backup <<'EOF'
#!/bin/sh
cp /opt/apps/chaoxing-lib/data/shared.db \
   /opt/apps/chaoxing-lib/data/shared-$(date +\%F).db
find /opt/apps/chaoxing-lib/data -name 'shared-*.db' -mtime +7 -delete
EOF
chmod 755 /etc/cron.daily/chaoxing-lib-backup
```

## 注意事项

- 服务只监听 127.0.0.1，**不要**在阿里云安全组里额外放行 5001。
- 域名 A 记录尚未指向本机，在此之前只能通过 SSH 隧道或内网验证。
- 数据库是单文件 SQLite，几十万题以内不需要换 PostgreSQL。
- 令牌泄露后改 `env` 里的 `LIB_TOKEN` 并 `systemctl restart chaoxing-lib`，客户端同步更换。