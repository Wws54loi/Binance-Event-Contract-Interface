#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 ADB 点击功能
"""

import subprocess
import os
import time

def test_adb_click():
    """测试 ADB 点击"""
    device_id = "40f06c22"
    adb_path = os.path.join(os.getcwd(), 'android-tools', 'platform-tools', 'adb.exe')
    click_coords = (416, 2452)
    
    print("=" * 50)
    print("🧪 ADB 点击测试")
    print("=" * 50)
    print(f"📱 设备ID: {device_id}")
    print(f"📍 点击坐标: {click_coords}")
    print(f"🔧 ADB路径: {adb_path}")
    print("-" * 50)
    
    # 1. 检查 ADB 路径
    if os.path.exists(adb_path):
        print("✅ ADB 文件存在")
    else:
        print(f"❌ ADB 文件不存在: {adb_path}")
        return
    
    # 2. 检查设备连接
    print("\n📡 检查设备连接...")
    check_cmd = [adb_path, 'devices']
    result = subprocess.run(check_cmd, capture_output=True, text=True)
    print(result.stdout)
    
    if device_id in result.stdout:
        print(f"✅ 设备 {device_id} 已连接")
    else:
        print(f"❌ 设备 {device_id} 未找到")
        return
    
    # 3. 执行3次测试点击
    print("\n🖱️ 开始点击测试（每次间隔2秒）...")
    x, y = click_coords
    
    for i in range(1, 4):
        print(f"\n第 {i} 次点击...")
        full_cmd = [adb_path, '-s', device_id, 'shell', 'input', 'tap', str(x), str(y)]
        
        print(f"执行命令: {' '.join(full_cmd)}")
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print(f"✅ 点击成功 #{i} - 坐标({x},{y})")
        else:
            print(f"❌ 点击失败: {result.stderr}")
            if result.stdout:
                print(f"输出: {result.stdout}")
        
        if i < 3:
            print("等待 2 秒...")
            time.sleep(2)
    
    print("\n" + "=" * 50)
    print("🏁 测试完成！")
    print("=" * 50)
    print("\n💡 提示：")
    print("   如果您的手机在坐标(416, 2452)处被点击了3次，说明功能正常！")
    print("   如果没有反应，请检查：")
    print("   1. 手机是否解锁")
    print("   2. USB调试是否已授权")
    print("   3. 坐标是否正确")

if __name__ == "__main__":
    try:
        test_adb_click()
    except KeyboardInterrupt:
        print("\n\n🛑 测试中断")
    except Exception as e:
        print(f"\n❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()
