from 手机控制 import PhoneController
import time

def test():
    print("📱 初始化手机控制器...")
    phone = PhoneController()
    
    print("\n⬇️ 准备测试向下滑动 (Swipe Down)...")
    print("请确保手机已连接并处于可以滑动的界面 (如资讯列表)")
    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)
        
    # 测试向下滑动
    phone.tap(200, 2436)
    
    print("\n✅ 测试完成")

if __name__ == "__main__":
    test()
