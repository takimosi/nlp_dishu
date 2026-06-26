from sentence_transformers import SentenceTransformer, util
import os

# 获取当前文件所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 本地模型路径（模型文件放在 game/models/ 目录下）
model_path = os.path.join(current_dir, 'models', 'paraphrase-multilingual-MiniLM-L12-v2')

# 如果本地模型不存在，则自动下载到本地
if not os.path.exists(model_path):
    print("本地模型不存在，正在下载...")
    print(f"目标路径: {model_path}")
    os.makedirs(model_path, exist_ok=True)
    # 使用 cache_folder 下载到指定目录
    model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', cache_folder=model_path)
    print("模型下载完成！")
else:
    print(f"加载本地模型: {model_path}")
    model = SentenceTransformer(model_path)

print("BERT 模型加载完成！")


def calculate_similarity(player_translation: str, reference_translation: str) -> float:
    """计算玩家翻译与标准答案的语义相似度，返回 0-1 之间的分数"""
    if not player_translation or not reference_translation:
        return 0.0

    emb1 = model.encode(player_translation, convert_to_tensor=True)
    emb2 = model.encode(reference_translation, convert_to_tensor=True)
    similarity = util.pytorch_cos_sim(emb1, emb2).item()

    return round(similarity, 4)


if __name__ == "__main__":
    print("\n测试模型...")
    print(f"『不屑』vs『不屑』: {calculate_similarity('不屑', '不屑')}")
    print(f"『因为穷而放弃』vs『不屑』: {calculate_similarity('因为穷而放弃', '不屑')}")
    print("测试完成！")