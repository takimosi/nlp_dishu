#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
画猜接龙 + 合作解谜 · 多人游戏后端
端口: 5001
"""

import json
import os
import uuid
import random
import string
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='templates')

# CORS 配置
CORS(app, resources={r"/api/*": {"origins": "*"}})


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response


# CORS 预检请求处理
@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    return '', 200


# 导入 BERT 评分模块
try:
    from bert_scorer import calculate_similarity

    BERT_AVAILABLE = True
    print("✅ BERT 评分模块已加载")
except ImportError as e:
    BERT_AVAILABLE = False
    print(f"⚠️ BERT 评分模块未找到: {e}")

# 数据存储
ROOMS_FILE = os.path.join(os.path.dirname(__file__), 'chain_rooms.json')
rooms = {}

# 启动时的题库（画猜接龙专用）
PROMPTS = [
    "猫", "狗", "下雨", "太阳", "咖啡", "睡觉", "吃饭", "跑步", "开心", "伤心",
    "上班", "放假", "手机", "电脑", "飞机", "火车", "大海", "森林", "月亮", "星星",
    "今天心情不错", "我饿了想吃饭", "上班要迟到了", "周末去公园散步", "收到礼物很开心",
    "熬夜工作好累", "和好朋友吵架了", "第一次坐飞机", "下雨天忘记带伞", "咖啡真苦",
    "猫在窗台上晒太阳", "手机没电了", "地铁上人好多", "做了一个奇怪的梦"
]

ROLE_NAMES = {
    'pos_like_category': '词性专家',
    'pragmatic_meaning': '语境专家',
    'morphological_features': '形态专家'
}


def save_rooms():
    try:
        with open(ROOMS_FILE, 'w', encoding='utf-8') as f:
            data = {}
            for rid, room in rooms.items():
                room_copy = room.copy()
                if 'created_at' in room_copy:
                    room_copy['created_at'] = room_copy['created_at'].isoformat()
                data[rid] = room_copy
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存房间失败: {e}")


def load_rooms():
    global rooms
    if os.path.exists(ROOMS_FILE):
        try:
            with open(ROOMS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for rid, room in data.items():
                    rooms[rid] = room
                    if 'created_at' in rooms[rid]:
                        rooms[rid]['created_at'] = datetime.fromisoformat(rooms[rid]['created_at'])
        except Exception as e:
            print(f"加载房间失败: {e}")


def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


# ==================== 通用房间操作 ====================

def create_room(host_name, max_players=6, game_mode='chain'):
    room_code = generate_room_code()
    player_id = str(uuid.uuid4())[:8]

    room = {
        'room_code': room_code,
        'host': host_name,
        'players': [{
            'id': player_id,
            'name': host_name,
            'is_ready': False,
            'order': 0
        }],
        'player_order': [player_id],
        'max_players': max_players,
        'status': 'waiting',
        'game_mode': game_mode,
        'created_at': datetime.now(),
    }

    # 画猜接龙模式特有字段
    if game_mode == 'chain':
        room.update({
            'current_turn': 0,
            'current_task': None,
            'current_prompt': None,
            'chain': [],
            'original_prompt': None,
            'total_rounds': 0,
            'final_score': None
        })

    # 合作解谜模式特有字段
    elif game_mode == 'coop':
        room.update({
            'coop_phase': 'waiting',
            'coop_role': {},
            'coop_role_assigned': False,
            'coop_round1_submitted': [],
            'coop_round2_submitted': [],
            'coop_round3_submitted': [],
            'coop_round1_data': {},
            'coop_round2_data': {},
            'coop_round3_data': {},
            'coop_scores': {},
            'current_question': None,
            'current_question_index': None,
            'context_prev': None,
            'context_next': None
        })

    rooms[room_code] = room
    save_rooms()
    return room_code


def join_room(room_code, player_name, game_mode=None):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"

    if room['status'] != 'waiting':
        return False, "游戏已经开始，无法加入"

    if len(room['players']) >= room['max_players']:
        return False, "房间已满"

    if game_mode and room.get('game_mode') != game_mode:
        return False, f"游戏模式不匹配"

    for p in room['players']:
        if p['name'] == player_name:
            player_name = f"{player_name}_{random.randint(10, 99)}"

    player_id = str(uuid.uuid4())[:8]
    room['players'].append({
        'id': player_id,
        'name': player_name,
        'is_ready': False,
        'order': len(room['players'])
    })
    save_rooms()
    return True, player_id


def leave_room(room_code, player_id):
    room = rooms.get(room_code)
    if not room:
        return
    room['players'] = [p for p in room['players'] if p['id'] != player_id]
    if len(room['players']) == 0:
        del rooms[room_code]
    else:
        if room['host'] not in [p['name'] for p in room['players']]:
            room['host'] = room['players'][0]['name']
    save_rooms()


# ==================== 画猜接龙模式（保持不变）====================

def start_chain_game(room_code):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"
    if len(room['players']) < 2:
        return False, "至少需要2名玩家"

    room['player_order'] = [p['id'] for p in room['players']]
    room['status'] = 'playing'

    original_prompt = random.choice(PROMPTS)
    room['original_prompt'] = original_prompt

    room['current_turn'] = 0
    room['current_task'] = 'draw'
    room['current_prompt'] = original_prompt

    room['total_rounds'] = len(room['players']) * 2

    room['chain'] = [{
        'round': 0,
        'type': 'start',
        'content': original_prompt,
        'author': '系统',
        'description': '原始词语'
    }]

    save_rooms()
    return True, "游戏开始"


def submit_chain_draw(room_code, player_id, icons):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"

    player_order = room.get('player_order', [])
    if not player_order or room['current_turn'] >= len(player_order):
        return False, "游戏状态异常"

    current_player_id = player_order[room['current_turn']]
    if current_player_id != player_id:
        return False, "不是你的回合"

    if room['current_task'] != 'draw':
        return False, "当前不是画图阶段"

    current_player = next((p for p in room['players'] if p['id'] == player_id), None)

    room['chain'].append({
        'round': len(room['chain']),
        'type': 'draw',
        'content': icons,
        'author': current_player['name'],
        'player_id': player_id,
        'timestamp': datetime.now().isoformat()
    })

    if len(room['chain']) >= room['total_rounds']:
        room['status'] = 'finished'
        final_guess = get_final_guess(room)
        if final_guess and BERT_AVAILABLE:
            room['final_score'] = calculate_similarity(final_guess, room['original_prompt'])
        else:
            room['final_score'] = 0
        save_rooms()
        return True, "游戏结束"

    room['current_task'] = 'guess'
    room['current_turn'] = (room['current_turn'] + 1) % len(room['players'])
    room['current_prompt'] = None
    save_rooms()
    return True, "提交成功"


def submit_chain_guess(room_code, player_id, text):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"

    player_order = room.get('player_order', [])
    if not player_order or room['current_turn'] >= len(player_order):
        return False, "游戏状态异常"

    current_player_id = player_order[room['current_turn']]
    if current_player_id != player_id:
        return False, "不是你的回合"

    if room['current_task'] != 'guess':
        return False, "当前不是猜图阶段"

    current_player = next((p for p in room['players'] if p['id'] == player_id), None)

    room['chain'].append({
        'round': len(room['chain']),
        'type': 'guess',
        'content': text,
        'author': current_player['name'],
        'player_id': player_id,
        'timestamp': datetime.now().isoformat()
    })

    if len(room['chain']) >= room['total_rounds']:
        room['status'] = 'finished'
        final_guess = get_final_guess(room)
        if final_guess and BERT_AVAILABLE:
            room['final_score'] = calculate_similarity(final_guess, room['original_prompt'])
        else:
            room['final_score'] = 0
        save_rooms()
        return True, "游戏结束"

    room['current_task'] = 'draw'
    room['current_turn'] = (room['current_turn'] + 1) % len(room['players'])
    room['current_prompt'] = text
    save_rooms()
    return True, "提交成功"


def get_final_guess(room):
    for item in reversed(room['chain']):
        if item['type'] == 'guess':
            return item['content']
    return None


def get_chain_room_state(room_code, player_id=None):
    room = rooms.get(room_code)
    if not room:
        return None

    current_player = None
    player_order = room.get('player_order', [])
    if player_order and room['current_turn'] < len(player_order):
        current_player_id = player_order[room['current_turn']]
        current_player = next((p for p in room['players'] if p['id'] == current_player_id), None)

    last_item = room['chain'][-1] if room['chain'] else None

    state = {
        'room_code': room['room_code'],
        'host': room['host'],
        'players': room['players'],
        'player_order': player_order,
        'max_players': room['max_players'],
        'status': room['status'],
        'game_mode': room.get('game_mode', 'chain'),
        'current_turn': room['current_turn'],
        'current_task': room['current_task'],
        'current_prompt': room['current_prompt'],
        'original_prompt': room.get('original_prompt'),
        'chain': room.get('chain', []),
        'last_item': last_item,
        'total_rounds': room.get('total_rounds', 0),
        'final_score': room.get('final_score'),
        'is_my_turn': False,
        'current_player_name': current_player['name'] if current_player else None
    }

    if player_id and room['status'] == 'playing':
        state['is_my_turn'] = current_player and current_player['id'] == player_id

    return state


# ==================== 合作解谜模式 ====================

def load_game_questions():
    """加载 game_final.json，并将图标路径转换为可访问的URL"""
    game_final_path = os.path.join(os.path.dirname(__file__), 'game_final.json')
    try:
        with open(game_final_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            questions = data.get('questions', [])

            # 将图标路径转换为可访问的URL
            for q in questions:
                if 'icon_images' in q:
                    new_icons = []
                    for icon_path in q['icon_images']:
                        # 将 ./auto_cut_segments/xxx.jpg 转换为 /game/auto_cut_segments/xxx.jpg
                        filename = os.path.basename(icon_path)
                        new_icons.append(f'/game/auto_cut_segments/{filename}')
                    q['icon_images'] = new_icons
            return questions
    except Exception as e:
        print(f"加载 game_final.json 失败: {e}")
        return []


GAME_QUESTIONS = load_game_questions()
print(f"✅ 加载了 {len(GAME_QUESTIONS)} 个合作解谜题目")

import os
import random


def get_random_context_icons(base_index, all_icons_count, count=3):
    """基于当前图标索引，随机获取前后相邻的图标"""
    indices = set()
    # 优先选择相邻的索引
    possible_indices = []
    for offset in range(-5, 6):
        if offset != 0:
            idx = base_index + offset
            if 0 <= idx < all_icons_count:
                possible_indices.append(idx)

    # 随机打乱后选取指定数量
    random.shuffle(possible_indices)
    selected_indices = possible_indices[:count]

    # 如果不够，从更远的地方补
    if len(selected_indices) < count:
        all_indices = list(range(all_icons_count))
        random.shuffle(all_indices)
        for idx in all_indices:
            if idx not in selected_indices and idx != base_index:
                selected_indices.append(idx)
                if len(selected_indices) >= count:
                    break

    return sorted(selected_indices)


# 在 start_coop_game 函数中，为每个玩家生成不同的上下文图标
def start_coop_game(room_code):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"
    if len(room['players']) < 2:
        return False, "至少需要2名玩家"
    if len(room['players']) > 3:
        return False, "合作模式最多支持3名玩家"

    # 分配角色（按加入顺序）
    roles = ['pos_like_category', 'pragmatic_meaning', 'morphological_features']

    for i, p in enumerate(room['players']):
        role_task = roles[i % len(roles)]
        room['coop_role'][p['id']] = {
            'task': role_task,
            'name': ROLE_NAMES[role_task],
            'order': i
        }

    # 随机选一个题目
    if GAME_QUESTIONS:
        idx = random.randint(0, len(GAME_QUESTIONS) - 1)
        room['current_question_index'] = idx
        room['current_question'] = GAME_QUESTIONS[idx]

        # 获取当前序列的起始图标编号（用于定位上下文）
        current_icons = room['current_question'].get('icon_images', [])
        if current_icons:
            # 从第一个图标的文件名提取编号
            first_icon = current_icons[0]
            # 提取文件名中的数字，如 "011009.jpg" -> 11009
            import re
            match = re.search(r'(\d+)', first_icon)
            if match:
                base_num = int(match.group(1))
                # 获取 auto_cut_segments 文件夹中的图片列表
                segments_dir = os.path.join(os.path.dirname(__file__), 'auto_cut_segments')
                if os.path.exists(segments_dir):
                    all_images = sorted([f for f in os.listdir(segments_dir) if f.endswith('.jpg')])
                    all_icons_count = len(all_images)

                    # 为每个玩家生成不同的上下文图标
                    context_map = {}
                    for p in room['players']:
                        # 每个玩家有不同的随机偏移
                        offset = p['order'] * 7  # 不同玩家不同的偏移量
                        context_base = base_num + offset
                        # 随机选取3-5张上下文图标
                        context_count = random.randint(3, 5)
                        context_indices = get_random_context_icons(context_base, all_icons_count, context_count)
                        context_icons = [f'/game/auto_cut_segments/{all_images[i]}' for i in context_indices if
                                         i < len(all_images)]
                        context_map[p['id']] = {
                            'icon_images': context_icons,
                            'description': f'上下文图标（共{len(context_icons)}张）'
                        }
                    room['player_contexts'] = context_map
    else:
        # 降级处理...
        room['current_question'] = {...}
        room['player_contexts'] = {}

    room['coop_role_assigned'] = True
    room['status'] = 'playing'
    room['coop_phase'] = 'round1'
    room['coop_round1_submitted'] = []
    room['coop_round2_submitted'] = []
    room['coop_round3_submitted'] = []
    room['coop_round1_data'] = {}
    room['coop_round2_data'] = {}
    room['coop_round3_data'] = {}
    room['coop_scores'] = {}

    save_rooms()
    return True, "游戏开始"


def submit_coop_round1(room_code, player_id, content):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"

    if room['coop_phase'] != 'round1':
        return False, "当前不是第1轮"

    if player_id in room['coop_round1_submitted']:
        return False, "你已经提交过了"

    player_name = next((p['name'] for p in room['players'] if p['id'] == player_id), '未知')
    room['coop_round1_data'][player_id] = {
        'content': content,
        'name': player_name,
        'role': room['coop_role'][player_id]['name']
    }
    room['coop_round1_submitted'].append(player_id)

    if len(room['coop_round1_submitted']) >= len(room['players']):
        room['coop_phase'] = 'round2'

    save_rooms()
    return True, "提交成功"


def submit_coop_round2(room_code, player_id, content):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"

    if room['coop_phase'] != 'round2':
        return False, "当前不是第2轮"

    if player_id in room['coop_round2_submitted']:
        return False, "你已经提交过了"

    player_name = next((p['name'] for p in room['players'] if p['id'] == player_id), '未知')
    room['coop_round2_data'][player_id] = {
        'content': content,
        'name': player_name,
        'role': room['coop_role'][player_id]['name']
    }
    room['coop_round2_submitted'].append(player_id)

    if len(room['coop_round2_submitted']) >= len(room['players']):
        room['coop_phase'] = 'round3'

    save_rooms()
    return True, "提交成功"


def submit_coop_round3(room_code, player_id, content):
    room = rooms.get(room_code)
    if not room:
        return False, "房间不存在"

    if room['coop_phase'] != 'round3':
        return False, "当前不是第3轮"

    if player_id in room['coop_round3_submitted']:
        return False, "你已经提交过了"

    player_name = next((p['name'] for p in room['players'] if p['id'] == player_id), '未知')

    # 获取正确答案
    current_q = room.get('current_question', {})
    reference = current_q.get('reference_translation', '')

    score = calculate_similarity(content, reference) if BERT_AVAILABLE and reference else 0.5

    room['coop_round3_data'][player_id] = {
        'content': content,
        'name': player_name,
        'role': room['coop_role'][player_id]['name'],
        'score': score
    }
    room['coop_round3_submitted'].append(player_id)
    room['coop_scores'][player_id] = score

    if len(room['coop_round3_submitted']) >= len(room['players']):
        room['coop_phase'] = 'finished'
        room['status'] = 'finished'

    save_rooms()
    return True, "提交成功"


def get_coop_room_state(room_code, player_id=None):
    room = rooms.get(room_code)
    if not room:
        return None

    # 获取当前玩家的专属上下文
    player_context = room.get('player_contexts', {}).get(player_id, {}) if player_id else {}

    state = {
        'room_code': room['room_code'],
        'host': room['host'],
        'players': room['players'],
        'max_players': room['max_players'],
        'status': room['status'],
        'game_mode': room.get('game_mode', 'coop'),
        'coop_phase': room.get('coop_phase', 'waiting'),
        'coop_role_assigned': room.get('coop_role_assigned', False),
        'coop_role': room.get('coop_role', {}),
        'coop_round1_submitted': room.get('coop_round1_submitted', []),
        'coop_round2_submitted': room.get('coop_round2_submitted', []),
        'coop_round3_submitted': room.get('coop_round3_submitted', []),
        'coop_round1_data': room.get('coop_round1_data', {}),
        'coop_round2_data': room.get('coop_round2_data', {}),
        'coop_round3_data': room.get('coop_round3_data', {}),
        'coop_scores': room.get('coop_scores', {}),
        'current_question': room.get('current_question'),
        'player_context_icons': player_context.get('icon_images', []),  # 当前玩家的专属上下文图标
        'context_description': player_context.get('description', '')
    }

    return state


def get_coop_annotation(room_code, player_id):
    room = rooms.get(room_code)
    if not room:
        return None, None, None

    role = room.get('coop_role', {}).get(player_id)
    if not role:
        return None, None, None

    current_q = room.get('current_question', {})
    annotations = current_q.get('annotations', {})

    annotation = annotations.get(role['task'], '无数据')

    # 调试日志
    print(
        f"get_coop_annotation: room={room_code}, player={player_id}, role={role['name']}, task={role['task']}, annotation={annotation}")

    return role['name'], role['task'], annotation


def get_coop_icons(room_code):
    """获取当前题目的图标序列（给玩家B）"""
    room = rooms.get(room_code)
    if not room:
        return []

    current_q = room.get('current_question')
    if not current_q:
        return []

    return current_q.get('icon_images', [])


def get_coop_context(room_code, player_id):
    """获取私有上下文（前后题目，不包含当前图标）"""
    room = rooms.get(room_code)
    if not room:
        return '', ''

    # 如果游戏还没开始，返回空
    if room.get('coop_phase') == 'waiting' or room.get('status') != 'playing':
        return '', ''

    prev_q = room.get('context_prev')
    next_q = room.get('context_next')

    prev_text = ''
    next_text = ''

    if prev_q:
        prev_text = prev_q.get('reference_translation', '') or prev_q.get('annotations', {}).get('pragmatic_meaning',
                                                                                                 '')
    if next_q:
        next_text = next_q.get('reference_translation', '') or next_q.get('annotations', {}).get('pragmatic_meaning',
                                                                                                 '')

    return prev_text, next_text


# ==================== API 路由 ====================

@app.route('/')
def index():
    return send_from_directory('templates', 'chain_lobby.html')


@app.route('/api/room/create', methods=['POST'])
def api_create_room():
    data = request.json
    host_name = data.get('name', '玩家')
    max_players = data.get('max_players', 6)
    game_mode = data.get('game_mode', 'chain')
    room_code = create_room(host_name, max_players, game_mode)
    player_id = rooms[room_code]['players'][0]['id']
    return jsonify({'success': True, 'room_code': room_code, 'player_id': player_id})


@app.route('/api/room/join', methods=['POST'])
def api_join_room():
    data = request.json
    room_code = data.get('room_code', '').upper().strip()
    player_name = data.get('name', '玩家')
    game_mode = data.get('game_mode')
    success, result = join_room(room_code, player_name, game_mode)
    if success:
        return jsonify({'success': True, 'room_code': room_code, 'player_id': result})
    else:
        return jsonify({'success': False, 'error': result}), 400


@app.route('/api/room/leave', methods=['POST'])
def api_leave():
    data = request.json
    room_code = data.get('room_code')
    player_id = data.get('player_id')
    leave_room(room_code, player_id)
    return jsonify({'success': True})


@app.route('/api/room/start', methods=['POST'])
def api_start_game():
    data = request.json
    room_code = data.get('room_code')
    player_id = data.get('player_id')
    room = rooms.get(room_code)
    if not room:
        return jsonify({'success': False, 'error': '房间不存在'}), 400

    host_player = next((p for p in room['players'] if p['name'] == room['host']), None)
    if not host_player or host_player['id'] != player_id:
        return jsonify({'success': False, 'error': '只有房主可以开始游戏'}), 400

    if room.get('game_mode') == 'coop':
        success, msg = start_coop_game(room_code)
    else:
        success, msg = start_chain_game(room_code)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': msg}), 400


@app.route('/api/room/state', methods=['GET'])
def api_room_state():
    room_code = request.args.get('room_code')
    player_id = request.args.get('player_id')
    room = rooms.get(room_code)
    if not room:
        return jsonify({'success': False, 'error': '房间不存在'}), 400

    if room.get('game_mode') == 'coop':
        state = get_coop_room_state(room_code, player_id)
    else:
        state = get_chain_room_state(room_code, player_id)

    if not state:
        return jsonify({'success': False, 'error': '房间不存在'}), 400
    return jsonify({'success': True, 'state': state})


@app.route('/api/room/submit', methods=['POST'])
def api_submit():
    data = request.json
    room_code = data.get('room_code')
    player_id = data.get('player_id')
    action = data.get('action')
    content = data.get('content')
    room = rooms.get(room_code)

    if not room:
        return jsonify({'success': False, 'error': '房间不存在'}), 400

    if room.get('game_mode') == 'coop':
        if action == 'round1':
            success, msg = submit_coop_round1(room_code, player_id, content)
        elif action == 'round2':
            success, msg = submit_coop_round2(room_code, player_id, content)
        elif action == 'round3':
            success, msg = submit_coop_round3(room_code, player_id, content)
        else:
            return jsonify({'success': False, 'error': '无效操作'}), 400
    else:
        if action == 'draw':
            success, msg = submit_chain_draw(room_code, player_id, content)
        elif action == 'guess':
            success, msg = submit_chain_guess(room_code, player_id, content)
        else:
            return jsonify({'success': False, 'error': '无效操作'}), 400

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': msg}), 400


@app.route('/api/coop/annotation', methods=['GET'])
def api_coop_annotation():
    room_code = request.args.get('room_code')
    player_id = request.args.get('player_id')

    role_name, task, annotation = get_coop_annotation(room_code, player_id)
    if not role_name:
        return jsonify({'success': False, 'error': '角色未分配'}), 400

    return jsonify({
        'success': True,
        'role_name': role_name,
        'task': task,
        'annotation': annotation
    })


@app.route('/api/coop/icons', methods=['GET'])
def api_coop_icons():
    room_code = request.args.get('room_code')

    icons = get_coop_icons(room_code)
    room = rooms.get(room_code)
    current_q = room.get('current_question') if room else None

    return jsonify({
        'success': True,
        'icon_images': icons,
        'seg_sequence': current_q.get('seg_sequence', []) if current_q else []
    })


@app.route('/api/coop/context', methods=['GET'])
def api_coop_context():
    room_code = request.args.get('room_code')
    player_id = request.args.get('player_id')

    prev_text, next_text = get_coop_context(room_code, player_id)
    return jsonify({
        'success': True,
        'prev_context': prev_text,
        'next_context': next_text
    })


if __name__ == '__main__':
    load_rooms()
    print("=" * 50)
    print("🎮 多人游戏服务器启动")
    print("   端口: 5001")
    print("   支持模式: 画猜接龙 (chain) | 合作解谜 (coop)")
    print(f"   合作解谜题库: {len(GAME_QUESTIONS)} 题")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5001, debug=True)