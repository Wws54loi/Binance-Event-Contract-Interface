import threading
import requests

# === 通知工具 ===
def send_ntfy(msg, file_data=None, filename=None):
    # 将发送逻辑封装在内部函数中
    def _send():
        try:
            url = "https://ntfy.sh/bnb"
            if file_data:
                headers = {"Filename": filename}
                requests.put(url, data=file_data.encode('utf-8'), headers=headers, timeout=10)
            else:
                requests.post(url, data=msg.encode('utf-8'), timeout=5)
        except Exception as e:
            print(f"Ntfy error: {e}")

    # 启动后台线程发送，不阻塞主程序
    threading.Thread(target=_send, daemon=True).start()