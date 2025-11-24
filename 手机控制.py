import os
import subprocess
import time

class PhoneController:
    def __init__(self, device_id="40f06c22"):
        self.device_id = device_id
        # 获取当前文件所在目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 假设 android-tools 在上一级目录 (根据项目结构调整)
        # 项目根目录/手动监听/phone_controller.py -> 项目根目录/android-tools/...
        self.adb_path = os.path.join(current_dir, '..', 'android-tools', 'platform-tools', 'adb.exe')
        
        # 规范化路径
        self.adb_path = os.path.normpath(self.adb_path)
        
        # 如果找不到，尝试使用系统环境变量中的 adb
        if not os.path.exists(self.adb_path):
            # 尝试回退到 async_binance_clicker.py 所在的根目录逻辑
            root_adb = os.path.join(os.getcwd(), 'android-tools', 'platform-tools', 'adb.exe')
            if os.path.exists(root_adb):
                self.adb_path = root_adb
            else:
                print(f"⚠️ 未找到指定路径的 ADB: {self.adb_path}，尝试使用系统 ADB")
                self.adb_path = "adb"

    def run_adb(self, command):
        """执行ADB命令"""
        try:
            full_cmd = [self.adb_path, '-s', self.device_id] + command
            # print(f"Executing: {' '.join(full_cmd)}")
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=5)
            return result.returncode == 0, result.stdout, result.stderr
        except Exception as e:
            print(f"ADB Error: {e}")
            return False, "", str(e)

    def tap(self, x, y):
        """点击指定坐标"""
        success, _, err = self.run_adb(['shell', 'input', 'tap', str(x), str(y)])
        if success:
            print(f"👆 点击坐标: ({x}, {y})")
        else:
            print(f"❌ 点击失败: {err}")
        return success

    def swipe_up(self, duration=300):
        """向上滑动 (手指从下往上滑，内容下滚)"""
        # 假设屏幕分辨率宽1080，高2400，从 3/4 处滑到 1/4 处
        start_x, start_y = 540, 1800
        end_x, end_y = 540, 600
        success, _, err = self.run_adb(['shell', 'input', 'swipe', str(start_x), str(start_y), str(end_x), str(end_y), str(duration)])
        if success:
            print(f"⬆️ 向上滑动")
        else:
            print(f"❌ 滑动失败: {err}")
        return success

    def swipe_down(self, duration=300):
        """向下滑动 (手指从上往下滑，内容上滚)"""
        # 改为轻微滑动，避免触发下拉刷新
        # 假设屏幕分辨率宽1080，高2400，从屏幕中部向下滑动
        start_x, start_y = 540, 800
        end_x, end_y = 540, 1200
        success, _, err = self.run_adb(['shell', 'input', 'swipe', str(start_x), str(start_y), str(end_x), str(end_y), str(duration)])
        if success:
            print(f"⬇️ 轻微向下滑动")
        else:
            print(f"❌ 滑动失败: {err}")
        return success

    def input_text(self, text):
        """输入文本/数字"""
        # adb input text 不支持空格，需要特殊处理
        # 确保输入的是字符串
        text_str = str(text)
        success, _, err = self.run_adb(['shell', 'input', 'text', text_str])
        if success:
            print(f"⌨️ 输入内容: {text_str}")
        else:
            print(f"❌ 输入失败: {err}")
        return success

    def close_keyboard(self):
        """关闭键盘 (发送 Back 键)"""
        # KeyCode 4 is BACK
        success, _, err = self.run_adb(['shell', 'input', 'keyevent', '4'])
        if success:
            print(f"🔽 关闭键盘 (Back)")
        else:
            print(f"❌ 关闭键盘失败: {err}")
        return success
    
    def get_screen_resolution(self):
        """获取屏幕分辨率"""
        success, output, _ = self.run_adb(['shell', 'wm', 'size'])
        if success and output:
            # Output format: "Physical size: 1080x2400"
            try:
                if "Physical size:" in output:
                    parts = output.strip().split("Physical size: ")[1].split('x')
                    return int(parts[0]), int(parts[1])
            except Exception as e:
                print(f"获取分辨率失败: {e}")
        return None, None

    def get_touch_limits(self):
        """获取触摸屏的最大硬件坐标"""
        success, output, _ = self.run_adb(['shell', 'getevent', '-p'])
        if not success:
            return None, None

        max_x = None
        max_y = None

        # 简单的解析逻辑
        for line in output.splitlines():
            # 检查是否包含 max 信息
            if "max" in line:
                # 尝试提取 max 值
                # 格式示例: 0035  : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
                try:
                    parts = line.split(',')
                    for part in parts:
                        part = part.strip()
                        if part.startswith("max"):
                            val = int(part.split()[1])
                            
                            if "0035" in line or "ABS_MT_POSITION_X" in line:
                                max_x = val
                            elif "0036" in line or "ABS_MT_POSITION_Y" in line:
                                max_y = val
                except:
                    pass
        
        return max_x, max_y

    def monitor_clicks(self):
        """
        监听点击坐标 (需要手动停止)
        自动尝试将硬件坐标转换为屏幕坐标。
        """
        print("👀 开始监听点击事件... (按 Ctrl+C 停止)")
        
        # 获取屏幕信息用于换算
        screen_w, screen_h = self.get_screen_resolution()
        max_x, max_y = self.get_touch_limits()
        
        if screen_w and max_x:
            print(f"ℹ️ 检测到屏幕分辨率: {screen_w}x{screen_h}")
            print(f"ℹ️ 检测到触控范围: X[0-{max_x}], Y[0-{max_y}]")
            print("✅ 将自动进行坐标转换")
        else:
            print("⚠️ 未能自动检测到屏幕或触控信息，将输出原始硬件坐标")

        # 启动 adb getevent
        cmd = [self.adb_path, '-s', self.device_id, 'shell', 'getevent', '-l']
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, encoding='utf-8', errors='ignore')

        try:
            last_x = None
            last_y = None
            
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                
                line = line.strip()
                
                # 解析 X 坐标
                if "ABS_MT_POSITION_X" in line:
                    parts = line.split()
                    if parts:
                        try:
                            last_x = int(parts[-1], 16)
                        except ValueError:
                            pass
                            
                # 解析 Y 坐标
                elif "ABS_MT_POSITION_Y" in line:
                    parts = line.split()
                    if parts:
                        try:
                            last_y = int(parts[-1], 16)
                        except ValueError:
                            pass
                
                # 监听抬起事件
                elif "BTN_TOUCH" in line and "UP" in line:
                    if last_x is not None and last_y is not None:
                        final_x, final_y = last_x, last_y
                        
                        # 如果可以换算，则进行换算
                        if screen_w and max_x and max_y:
                            final_x = int((last_x / max_x) * screen_w)
                            final_y = int((last_y / max_y) * screen_h)
                            print(f"📍 点击坐标: ({final_x}, {final_y}) [原始: {last_x}, {last_y}]")
                        else:
                            print(f"📍 点击坐标(原始): ({last_x}, {last_y})")
                
        except KeyboardInterrupt:
            print("\n🛑 停止监听")
        finally:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

    def key_event(self, key_code):
        """发送按键事件"""
        success, _, err = self.run_adb(['shell', 'input', 'keyevent', str(key_code)])
        return success

    def scroll_to_top(self, steps=5):
        """
        回到顶部 (通过连续轻微滑动)
        利用 swipe_down 的安全区域逻辑，连续滑动多次以回到顶部，同时避免触发下拉刷新。
        """
        print(f"🔝 正在回到顶部 (执行 {steps} 次轻微滑动)...")
        for _ in range(steps):
            self.swipe_down(duration=150)
            time.sleep(0.1)
        return True

    def try_home_key(self):
        """
        尝试使用 HOME 键回到顶部 (部分 App 支持)
        """
        print("🔝 尝试发送 HOME 键 (KeyCode 122)...")
        return self.key_event(122)

# 测试代码
if __name__ == "__main__":
    print("📱 初始化手机控制器...")
    controller = PhoneController()
    
    # 示例用法
    # print("测试点击...")
    # controller.tap(500, 500)
    
    # print("测试滑动...")
    # controller.swipe_up()
    
    # print("测试输入...")
    # controller.input_text("100")
    
    # print("测试关闭键盘...")
    # controller.close_keyboard()

    # print("测试监听点击 (按 Ctrl+C 停止)...")
    # controller.monitor_clicks()

    # 测试回到顶部
    controller.scroll_to_top()

    # 尝试 HOME 键
    controller.try_home_key()
