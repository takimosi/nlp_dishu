from flask import Flask, render_template, session, request, jsonify, send_from_directory
import json
import os
import random
from bert_scorer import calculate_similarity
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-game-secret-key')

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
) if DEEPSEEK_API_KEY else None


# 让 Flask 可以访问 auto_cut_segments 文件夹
@app.route('/auto_cut_segments/<path:filename>')
def serve_image(filename):
    return send_from_directory('auto_cut_segments', filename)


# 加载最终游戏数据
with open('game_final.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
    questions = data['questions']

# 加载图片顺序
with open('image_order.json', 'r', encoding='utf-8') as f:
    image_order = json.load(f)

print(f"✅ 加载了 {len(questions)} 个游戏题目")
print(f"✅ 加载了 {len(image_order)} 张图片顺序")


def get_context_images(current_seg_nums, context_size=2):
    """
    获取上下文图片
    current_seg_nums: 当前序列的数字列表，如 [11009, 11010, 11011]
    context_size: 前后各取几张
    返回: (前文图片路径列表, 当前图片路径列表, 后文图片路径列表)
    """
    if not current_seg_nums:
        return [], [], []

    first_num = current_seg_nums[0]
    last_num = current_seg_nums[-1]

    try:
        first_idx = image_order.index(first_num)
        last_idx = image_order.index(last_num)
    except ValueError:
        return [], [], []

    # 前文：当前序列之前的 context_size 个
    prev_start = max(0, first_idx - context_size)
    prev_nums = image_order[prev_start:first_idx]
    prev_images = [f"/auto_cut_segments/{num:06d}.jpg" for num in prev_nums]

    # 当前序列的图片
    current_images = [f"/auto_cut_segments/{num:06d}.jpg" for num in current_seg_nums]

    # 后文：当前序列之后的 context_size 个
    next_end = min(len(image_order), last_idx + context_size + 1)
    next_nums = image_order[last_idx + 1:next_end]
    next_images = [f"/auto_cut_segments/{num:06d}.jpg" for num in next_nums]

    return prev_images, current_images, next_images


def generate_fake_annotation(real_annotation, max_retries=2):
    """
    调用 DeepSeek API 生成伪造标注
    real_annotation: 真实的 pragmatic_meaning 标注文本
    返回: 伪造的标注文本
    """
    prompt = f"""你是一个地书符号的标注伪造者。你的任务是根据真实标注，生成一个看起来合理但实际错误的伪造标注。

【真实标注】
{real_annotation}

【伪造规则】
1. 只能改动 1-2 个关键信息点（不能全改，否则太假）
2. 保持相同的格式和描述方式（逐图描述）
3. 改动要看起来合理，但实际偏离原意
4. 不要使用否定词直接否定（如"不是XX"），而是改成另一个合理的含义

【输出要求】
只输出伪造后的标注文本，不要输出任何解释。

示例：
真实标注：图1: 人物从一处向另一处移动，图2: 含酒精的饮品，图3: 表达喜欢的心情
伪造输出：图1: 人物在某处静止站立，图2: 碳酸饮料，图3: 表达愤怒的情绪

请生成："""

    for attempt in range(max_retries):
        if client is None:
            break
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "你是一个地书符号标注的伪造专家，只输出伪造后的标注文本。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8,
                max_tokens=500
            )
            fake = response.choices[0].message.content.strip()
            # 基本验证：不能为空，不能和原文完全相同
            if fake and fake != real_annotation and len(fake) > 10:
                return fake
        except Exception as e:
            print(f"生成伪造标注失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            continue

    # 降级：返回一个基于规则的简单伪造
    return fallback_fake_annotation(real_annotation)


def fallback_fake_annotation(real_annotation):
    """降级方案：基于规则的简单伪造"""
    replacements = {
        "移动": "站立",
        "走": "停",
        "跑": "站",
        "喝": "看",
        "酒": "水",
        "喜欢": "讨厌",
        "开心": "难过",
        "进入": "离开",
        "出来": "进去",
    }
    fake = real_annotation
    for old, new in replacements.items():
        fake = fake.replace(old, new)
    if fake == real_annotation:
        fake = real_annotation + "（标注存疑）"
    return fake


# 角色对应的标注字段
ROLE_FIELDS = {
    'player_a': 'pos_like_category',
    'player_b': 'pragmatic_meaning',
    'player_c': 'morphological_features'
}

ROLE_NAMES = {
    'player_a': '📖 词性专家 (Q)',
    'player_b': '👁️ 描述专家 (K)',
    'player_c': '🔧 形态专家 (V)'
}


@app.route('/')
def index():
    return render_template('index.html', role_names=ROLE_NAMES)


@app.route('/mode1')
def mode1():
    """模式一：合作解谜"""
    current = random.choice(questions)

    # 提取当前序列的数字
    current_seg_nums = []
    for seg_id in current['seg_sequence']:
        num = int(seg_id.replace('seg_', ''))
        current_seg_nums.append(num)

    # 获取上下文图片
    prev_images, current_images, next_images = get_context_images(current_seg_nums, context_size=3)

    session['current_question'] = current
    session['current_context'] = {
        'prev_images': prev_images,
        'current_images': current_images,
        'next_images': next_images
    }

    return render_template('mode1_coop.html',
                           question=current,
                           context=session['current_context'],
                           role_names=ROLE_NAMES)


@app.route('/mode2')
def mode2():
    """模式二：谁是内鬼"""
    return render_template('mode2_spy.html')


@app.route('/api/spy_question', methods=['GET'])
def spy_question():
    """获取内鬼模式题目（包含真实标注 + 实时生成的伪造标注）"""
    # 筛选有 pragmatic_meaning 的题目
    valid_questions = [q for q in questions if q.get('annotations', {}).get('pragmatic_meaning')]
    if not valid_questions:
        valid_questions = [q for q in questions if q.get('reference_translation')]

    current = random.choice(valid_questions)

    # 提取真实标注
    real_annotation = current.get('annotations', {}).get('pragmatic_meaning')
    if not real_annotation:
        real_annotation = current.get('reference_translation', '地书符号序列')

    # 生成伪造标注
    fake_annotation = generate_fake_annotation(real_annotation)

    return jsonify({
        'seg_sequence': current['seg_sequence'],
        'icon_images': current['icon_images'],
        'annotations': {
            'pragmatic_meaning': real_annotation
        },
        'fake_annotation': fake_annotation
    })


@app.route('/api/submit_translation', methods=['POST'])
def submit_translation():
    """提交翻译并评分（单人）"""
    data = request.json
    translation = data.get('translation', '').strip()

    current = session.get('current_question')
    if not current:
        return jsonify({'error': '没有进行中的游戏'}), 400

    reference = current.get('reference_translation', '')

    if not translation:
        return jsonify({'score': 0, 'message': '请输入翻译'})

    score = calculate_similarity(translation, reference)

    if score >= 0.8:
        message = "🎉 非常准确！"
    elif score >= 0.6:
        message = "👍 基本正确，再接再厉！"
    elif score >= 0.4:
        message = "🤔 有点接近，但还需要调整"
    else:
        message = "💡 差别较大，再讨论一下？"

    return jsonify({'score': score, 'message': message, 'reference': reference})


@app.route('/api/batch_score', methods=['POST'])
def batch_score():
    """批量评分（第三轮结束后调用）"""
    data = request.json
    translations = data.get('translations', {})

    current = session.get('current_question')
    if not current:
        return jsonify({'error': '没有进行中的游戏'}), 400

    reference = current.get('reference_translation', '')

    scores = {}
    for player, translation in translations.items():
        if translation:
            score = calculate_similarity(translation, reference)
        else:
            score = 0
        scores[player] = score

    return jsonify({'scores': scores, 'reference': reference})


@app.route('/api/new_question', methods=['POST'])
def new_question():
    """获取新题目"""
    current = random.choice(questions)

    current_seg_nums = []
    for seg_id in current['seg_sequence']:
        num = int(seg_id.replace('seg_', ''))
        current_seg_nums.append(num)

    prev_images, current_images, next_images = get_context_images(current_seg_nums, context_size=3)

    session['current_question'] = current
    session['current_context'] = {
        'prev_images': prev_images,
        'current_images': current_images,
        'next_images': next_images
    }

    return jsonify({
        'seg_sequence': current['seg_sequence'],
        'icon_images': current['icon_images'],
        'context': session['current_context'],
        'annotations': {
            'pos_like_category': current['annotations']['pos_like_category'],
            'pragmatic_meaning': current['annotations']['pragmatic_meaning'],
            'morphological_features': current['annotations']['morphological_features']
        }
    })


if __name__ == '__main__':
    print("=" * 50)
    print("《标注者》游戏服务器启动")
    print(f"访问地址: http://127.0.0.1:5000")
    print(f"题目数量: {len(questions)}")
    print("=" * 50)
    app.run(debug=True)
