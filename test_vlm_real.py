import sys
import os
import json

# 将项目根目录加入 path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from tools.image import analyze_image, extract_text_from_image, identify_dish, review_image

def main():
    # 使用一个小红书风格的美食图片测试
    test_img = "https://images.unsplash.com/photo-1546069901-ba9599a7e63c?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80"
    
    print("="*50)
    print("1. 测试: analyze_image (全模态联合分析)")
    print("="*50)
    try:
        res = analyze_image([test_img], "请简短描述这道菜（50字以内）。")
        print(res)
    except Exception as e:
        print(f"Error: {e}")

    print("\n" + "="*50)
    print("2. 测试: identify_dish (识菜)")
    print("="*50)
    try:
        res = identify_dish(test_img)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Error: {e}")
        
    print("\n" + "="*50)
    print("3. 测试: extract_text_from_image (OCR)")
    print("="*50)
    try:
        # 使用带文字的图
        text_img = "https://images.unsplash.com/photo-1507842217343-583bb7270b66?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80"
        res = extract_text_from_image(text_img)
        print(res if res else "未提取到文字")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
