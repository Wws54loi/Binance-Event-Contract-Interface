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
    # phone.monitor_clicks()
    # phone.tap(463, 2712) #上涨
    # phone.tap(1025, 2714) #下跌
    phone.tap(563, 2260) #点击输入金额框
    phone.clear_text(10) #清空文本框 (假设原有内容不超过10个字符)
    phone.input_text(5) #点击输入金额
    phone.close_keyboard() #关闭键盘
    phone.scroll_to_top(3) #点击向上滑动
    # phone.tap(782, 3014) #点击确定按钮
    

    
    print("\n✅ 测试完成")

if __name__ == "__main__":
    test()


# 上涨(441, 2696)
# 下跌(1052, 2703)
# 输入金额(563, 2260)