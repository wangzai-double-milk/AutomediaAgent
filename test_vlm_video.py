import sys
import os

# 将项目根目录加入 path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from tools.image import analyze_image

def main():
    video_url = "https://www.w3schools.com/html/mov_bbb.mp4"
    print("="*50)
    print("测试视频输入是否会触发 API 参数报错...")
    print("="*50)
    try:
        res = analyze_image([video_url], "请简短描述这个视频。")
        print("请求成功发送（未报格式错误），返回结果：", repr(res))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
