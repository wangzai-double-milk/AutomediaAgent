import sys
import os

# 将项目根目录加入 path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from tools.image import analyze_image

video_url = "https://www.w3schools.com/html/mov_bbb.mp4" # 测试用视频 URL
prompt = "分析这个视频的内容，这是关于什么的？"

print("开始测试火山引擎全模态模型对视频 URL 的支持...")
try:
    result = analyze_image([video_url], prompt)
    print("测试成功！模型返回：")
    print(result)
except Exception as e:
    print("测试失败，报错如下：")
    import traceback
    traceback.print_exc()
