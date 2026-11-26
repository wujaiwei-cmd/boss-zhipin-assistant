# -*- coding: utf-8 -*-
"""
job_analysis.py —— 公司与岗位分析引擎

两条路线：
1. local：不联网、不花钱。用 JD 关键词 + 岗位能力模型 + 简历关键词做结构化分析，
   输出公司解读、岗位画像、匹配度、简历改法、学习计划。
2. llm：调用 OpenAI 兼容接口（默认 DeepSeek），把 JD、公司信息、简历和本地初筛
   一起喂给模型，得到更深入的公司 / 岗位判断；失败时自动降级回本地报告。

所有分析结果都是 Markdown 文本，可直接展示、保存到 reports/。
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime

from job_kb import (
    INDUSTRY_NOTES, PROJECT_IDEAS, RESUME_ISSUES, RISK_SIGNALS,
    SIZE_NOTES, SKILLS, SKILL_INDEX, STAGE_NOTES, role_for_title,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
PROFILE_PATH = os.path.join(BASE_DIR, 'profile.json')
REPORT_DIR = os.path.join(BASE_DIR, 'reports')

DEFAULT_CONFIG = {
    'llm_base_url': 'https://api.deepseek.com/v1',
    'llm_model': 'deepseek-chat',
    'llm_api_key': '',
    'llm_timeout': 120,
    'llm_max_tokens': 2200,
    'auto_analyze': True,
    'auto_engine': 'local',       # local / llm
    'manual_engine': 'local',     # 分析标签页上次选择
}

DEFAULT_PROFILE = {
    'resume': '',
    'expect': '',
}


# ---------------------------------------------------------------------------
# 配置 / 简历档案
# ---------------------------------------------------------------------------
def _read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            out = dict(default)
            out.update(data)
            return out
    except (OSError, ValueError):
        pass
    return dict(default)


def _write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config():
    return _read_json(CONFIG_PATH, DEFAULT_CONFIG)


def save_config(cfg):
    data = dict(DEFAULT_CONFIG)
    data.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
    _write_json(CONFIG_PATH, data)
    return data


def load_profile():
    return _read_json(PROFILE_PATH, DEFAULT_PROFILE)


def save_profile(resume, expect=''):
    data = {'resume': resume or '', 'expect': expect or '',
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    _write_json(PROFILE_PATH, data)
    return data


def has_api_key(cfg=None):
    cfg = cfg or load_config()
    return bool((cfg.get('llm_api_key') or '').strip())


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------
_ALIAS_CACHE = {}


def _alias_pattern(alias):
    pat = _ALIAS_CACHE.get(alias)
    if pat is None:
        if re.search(r'[A-Za-z]', alias):
            pat = re.compile(r'(?<![A-Za-z0-9+#.])' + re.escape(alias) + r'(?![A-Za-z0-9+#])',
                             re.IGNORECASE)
        else:
            pat = re.compile(re.escape(alias))
        _ALIAS_CACHE[alias] = pat
    return pat


def clean_text(text):
    """统一空白、去掉零宽字符和常见页面噪音。"""
    if not text:
        return ''
    text = text.replace('\u200b', '').replace('\xa0', ' ')
    text = re.sub(r'\r\n?', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def find_skills(text):
    """返回 {技能名: 命中次数}。英文按单词边界，中文按包含。"""
    if not text:
        return {}
    hits = {}
    for skill in SKILLS:
        count = 0
        for alias in skill['alias']:
            count += len(_alias_pattern(alias).findall(text))
        if count:
            hits[skill['name']] = min(count, 5)
    return hits


def _find_note(text, table):
    """在文本里找阶段 / 规模的解读。"""
    for keys, note in table:
        for key in keys:
            if key in text:
                return key, note
    return '', ''


def _first_match(text, patterns):
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip(' ：:|·\n')
    return ''


def _industry_of(text):
    best = ''
    for ind in INDUSTRY_NOTES:
        if ind in text and len(ind) > len(best):
            best = ind
    return best


# ---------------------------------------------------------------------------
# 解析：职位 / 公司
# ---------------------------------------------------------------------------
SALARY_RE = re.compile(
    r'\d+(?:\.\d+)?\s*[Kk]\s*[-~—]\s*\d+(?:\.\d+)?\s*[Kk](?:\s*[·*xX×]\s*\d+\s*薪)?'
    r'|\d+(?:\.\d+)?(?:\s*[-~—]\s*\d+(?:\.\d+)?)?\s*[Kk千](?:\s*[·*xX×]\s*\d+\s*薪)?'
    r'|\d+(?:\.\d+)?(?:\s*[-~—]\s*\d+(?:\.\d+)?)?\s*元\s*[/每]\s*(?:天|小时|时|月)'
)
EXP_RE = re.compile(r'\d+\s*[-~—]\s*\d+\s*年|经验不限|应届生|在校/应届|\d+\s*年以上')
EDU_RE = re.compile(r'博士|硕士|本科|大专|中专|高中|学历不限')
SIZE_RE = re.compile(r'\d+\s*[-~—]\s*\d+\s*人|\d+\s*人以上|少于\s*\d+\s*人|\d+\s*人以下')
STAGE_RE = re.compile(r'天使轮|种子轮|[A-E]轮|已上市|不需要融资|战略融资|IPO')
ADDRESS_RE = re.compile(r'(?:工作地址|办公地址|公司地址|地址)\s*[:：]?\s*([^\n]{2,80})')
COMPANY_NAME_RE = re.compile(
    r'([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,30}'
    r'(?:股份有限公司|有限责任公司|有限公司|集团|研究院|事务所))'
)


def parse_job(raw_text, title_hint='', url=''):
    """把职位详情页文本拆成结构化字段。"""
    text = clean_text(raw_text)
    head = text[:1200]      # 薪资 / 经验 / 学历基本都在顶部，先只在顶部找，避免误命中公司简介
    job = {
        'title': (title_hint or '').strip(),
        'salary': '', 'exp': '', 'edu': '', 'location': '',
        'tags': [], 'jd_text': text, 'url': url,
        'responsibilities': [], 'requirements': [], 'bonus': [],
        'skills': {}, 'risk': [],
    }
    m = SALARY_RE.search(head) or SALARY_RE.search(text)
    if m:
        job['salary'] = re.sub(r'\s+', '', m.group(0))
    m = EXP_RE.search(head) or EXP_RE.search(text)
    if m:
        job['exp'] = re.sub(r'\s+', '', m.group(0))
    m = EDU_RE.search(head) or EDU_RE.search(text)
    if m:
        job['edu'] = m.group(0)
    m = ADDRESS_RE.search(text)
    if m:
        job['location'] = m.group(1).strip()

    if not job['title']:
        for line in text.split('\n')[:6]:
            line = line.strip()
            if 2 <= len(line) <= 30 and not SALARY_RE.search(line) \
                    and not re.match(r'^[\d\s\-·]+$', line) \
                    and not _section_kind(line) \
                    and not any(h in line for h in STOP_HEADS):
                job['title'] = re.split(r'\s{2,}|[|｜]', line)[0].strip()
                break

    resp, req, bonus = split_sections(text)
    job['responsibilities'] = resp
    job['requirements'] = req
    job['bonus'] = bonus
    job['skills'] = find_skills(text)

    joined = text
    for keys, tip in RISK_SIGNALS:
        for kw in keys:
            if kw in joined:
                job['risk'].append(tip)
                break
    return job


SECTION_HEADS = {
    'req': ['任职要求', '岗位要求', '任职资格', '职位要求', '技能要求', '任职条件', '你需要'],
    'resp': ['岗位职责', '工作职责', '职位描述', '工作内容', '岗位描述', '你将负责', '工作职责'],
    'bonus': ['加分项', '优先考虑', '福利待遇', '我们提供', '薪资福利'],
}

# 这些标题之后就不再是 JD 了（公司介绍 / 地址 / 页面推荐），必须截断
STOP_HEADS = ['公司介绍', '公司简介', '企业介绍', '公司地址', '工作地址', '工商信息',
              '招聘职位', '相似职位', '职位发布者', '举报', '公司福利', '职位亮点']


def _section_kind(line):
    s = line.strip(' ：:')
    if len(s) > 24:
        return None
    for kind, heads in SECTION_HEADS.items():
        for h in heads:
            if h in s:
                return kind
    return None


def split_sections(text):
    """按常见小标题拆分 JD，拿不到就按句子特征兜底。"""
    resp, req, bonus = [], [], []
    current = None
    buckets = {'resp': resp, 'req': req, 'bonus': bonus}
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if len(line) <= 30 and any(h in line for h in STOP_HEADS):
            current = None
            continue
        kind = _section_kind(line)
        if kind:
            current = kind
            # 标题行后面可能直接跟内容
            rest = line
            for h in SECTION_HEADS[kind]:
                rest = rest.replace(h, '')
            rest = rest.strip(' ：:')
            if len(rest) >= 6:
                buckets[kind].append(rest)
            continue
        if current:
            buckets[current].append(line)

    def _trim(lines, limit):
        out, seen = [], set()
        for x in lines:
            x = re.sub(r'^\s*(?:\d+[.、）)]|[-•·])\s*', '', x)
            x = x.strip(' ·-—；;，,。')
            if len(x) < 4:
                continue
            key = x[:24]
            if key in seen:
                continue
            seen.add(key)
            out.append(x)
            if len(out) >= limit:
                break
        return out

    resp, req, bonus = _trim(resp, 10), _trim(req, 12), _trim(bonus, 6)

    if not resp:
        resp = _trim([ln for ln in text.split('\n')
                      if len(ln) < 160 and re.search(r'负责|参与|推动|搭建|设计|维护|跟进|制定', ln)], 8)
    if not req:
        req = _trim([ln for ln in text.split('\n')
                     if len(ln) < 160 and re.search(r'熟悉|掌握|了解|具备|要求|经验|本科|大专|优先', ln)], 10)
    return resp, req, bonus


def parse_company(raw_text, name_hint='', industry_hint='', url=''):
    """把公司介绍 / 公司主页文本拆成结构化字段。"""
    text = clean_text(raw_text)
    company = {
        'name': (name_hint or '').strip(),
        'industry': (industry_hint or '').strip(),
        'size': '', 'stage': '', 'address': '', 'intro': '', 'url': url,
        'tags': '', 'founded': '', 'reg_capital': '', 'insured': '', 'scope': '',
    }
    if not company['industry']:
        company['industry'] = _industry_of(text)

    m = SIZE_RE.search(text)
    if m:
        company['size'] = re.sub(r'\s+', '', m.group(0))
    m = STAGE_RE.search(text)
    if m:
        company['stage'] = m.group(0)
    m = ADDRESS_RE.search(text)
    if m:
        company['address'] = m.group(1).strip()
    m = re.search(r'注册资本\s*[:：]?\s*([\d.,]+\s*(?:万|亿)?\s*(?:元人民币|元|人民币)?)', text)
    if m:
        company['reg_capital'] = re.sub(r'\s+', '', m.group(1))
    m = re.search(r'成立日期\s*[:：]?\s*(\d{4}\s*[-年./]\s*\d{1,2}\s*[-月./]\s*\d{1,2}\s*日?)', text)
    if m:
        company['founded'] = re.sub(r'\s+', '', m.group(1)).replace('/', '-').replace('.', '-')
    m = re.search(r'(?:参保人数|缴纳社保人数)\s*[:：]?\s*(\d+\s*人?)', text)
    if m:
        company['insured'] = re.sub(r'\s+', '', m.group(1))
    m = re.search(r'经营范围\s*[:：]?\s*(.{10,300}?)(?:\n|$)', text)
    if m:
        company['scope'] = m.group(1).strip()
    if not company['name']:
        m = COMPANY_NAME_RE.search(text)
        if m:
            company['name'] = m.group(1)

    intro = ''
    m = re.search(r'(?:公司介绍|公司简介|企业介绍|关于我们)\s*[:：]?\s*\n?(.{20,1200})', text, re.S)
    if m:
        intro = m.group(1).strip()
        # 截断到"工商信息/公司地址"等下一段标题
        intro = re.split(r'\n(?:工商信息|公司地址|工作地址|公司福利|招聘职位|相似职位)', intro)[0]
    elif len(text) > 60 and not re.search(r'职位描述|任职要求|岗位职责|岗位要求', text):
        intro = text
    company['intro'] = intro[:1200].strip()

    tags = [company['industry'], company['size'], company['stage']]
    company['tags'] = ' · '.join(t for t in dict.fromkeys(tags) if t)
    return company


# ---------------------------------------------------------------------------
# 简历诊断 / 匹配
# ---------------------------------------------------------------------------
STRONG_VERBS = ['主导', '搭建', '优化', '落地', '推动', '设计', '重构', '从0到1', '从 0 到 1',
                '提升', '降低', '缩短', '负责', '独立']


def diagnose_resume(resume):
    """返回 (问题列表, 统计信息)。问题用 RESUME_ISSUES 的文案。"""
    text = clean_text(resume)
    issues = []
    stats = {'chars': len(text), 'numbers': 0, 'verbs': 0, 'skills': 0}
    if not text:
        return ['尚未填写简历，无法评估匹配度。'], stats

    if stats['chars'] < 300:
        issues.append(RESUME_ISSUES['short'])
    elif stats['chars'] > 3000:
        issues.append(RESUME_ISSUES['long'])

    stats['numbers'] = len(re.findall(r'\d+(?:\.\d+)?\s*(?:%|万|亿|人|天|小时|分钟|秒|ms|QPS|倍)?', text))
    if stats['numbers'] < 4:
        issues.append(RESUME_ISSUES['no_number'])

    if not re.search(r'项目|工作经历|实习经历|主要业绩|工作内容', text):
        issues.append(RESUME_ISSUES['no_project'])

    strong = sum(text.count(v) for v in STRONG_VERBS)
    weak = sum(text.count(v) for v in ('参与', '协助', '帮忙'))
    stats['verbs'] = strong
    if strong < 3 and weak >= 2:
        issues.append(RESUME_ISSUES['weak_verb'])

    if '精通' in text:
        issues.append(RESUME_ISSUES['master_word'])

    if not re.search(r'技能|技术栈|专业技能|技能清单|掌握技能', text):
        issues.append(RESUME_ISSUES['no_skill_section'])
    stats['skills'] = len(find_skills(text))
    return issues, stats


# 同一组技能满足其一即可，避免把「Vue 和 React」同时算成缺口
ROLE_ALTERNATIVES = {
    '后端开发': [['Java', 'Go', 'Python', 'C++']],
    '前端开发': [['Vue', 'React']],
    '移动端开发': [['Android', 'iOS', 'Flutter']],
    '算法/机器学习': [['PyTorch', 'TensorFlow']],
    '测试/QA': [['自动化测试', '性能测试']],
    '销售': [['大客户销售', '渠道销售']],
}

ROLE_PROJECT_CAT = {
    '后端开发': '后端', '前端开发': '前端', '移动端开发': '移动端',
    '算法/机器学习': '数据/AI', '数据分析': '数据/AI', '数据开发': '数据/AI',
    '测试/QA': '测试', '运维/DevOps': '运维', '嵌入式/硬件': '嵌入式/硬件',
    '产品经理': '产品', '运营': '运营', '市场营销': '市场', '销售': '销售',
    '设计': '设计', '人力资源': '人力', '财务': '财务', '供应链/采购': '供应链',
    '项目管理': '通用', '通用/其他': '通用',
}


def _alt_groups(role):
    groups = []
    for names in ROLE_ALTERNATIVES.get(role['name'], []):
        names = [n for n in names if n in SKILL_INDEX]
        if names:
            groups.append(names)
    return groups


def requirement_groups(role, jd_skills):
    """把岗位要求拆成"满足其一"的分组，再补上 JD 额外点名和加分项。"""
    alts = _alt_groups(role)
    alt_members = {n for g in alts for n in g}
    fixed = [s for s in role['core'] if s in SKILL_INDEX and s not in alt_members]
    groups = [[s] for s in fixed] + alts
    covered = {n for g in groups for n in g}

    for s in jd_skills:
        if s in covered or s not in SKILL_INDEX:
            continue
        groups.append([s])
        covered.add(s)
    for s in role['bonus']:
        if s in covered or s not in SKILL_INDEX:
            continue
        groups.append([s])
        covered.add(s)
    return groups


def match_resume(job, company, resume):
    """技能关键词匹配度 + 缺口排序（按"技能组"计算，更贴近真实要求）。"""
    resume = clean_text(resume)
    jd_skills = dict(job.get('skills') or {})
    role = role_for_title(job.get('title', ''), job.get('jd_text', ''))
    groups = requirement_groups(role, jd_skills)
    resume_skills = find_skills(resume)

    core_names = set(role['core']) | {n for g in _alt_groups(role) for n in g}
    bonus_names = set(role['bonus'])

    # "了解 / 优先 / 加分"这类表述降低权重，避免把加分项算成硬门槛
    weak = set()
    for line in (job.get('jd_text') or '').split('\n'):
        if re.search(r'优先|加分|了解|可选|最好|更佳', line):
            weak.update(find_skills(line))

    reqs = []
    for names in groups:
        in_jd = any(n in jd_skills for n in names)
        jd_members = [n for n in names if n in jd_skills]
        group_weak = bool(jd_members) and all(n in weak for n in jd_members)
        if in_jd and set(names) & core_names and not group_weak:
            weight = 3.0
        elif group_weak:
            weight = 1.3
        elif in_jd:
            weight = 2.0
        elif set(names) <= bonus_names:
            weight = 0.8
        else:
            weight = 1.2
        hit = [n for n in names if n in resume_skills]
        reqs.append({'names': names, 'weight': weight, 'matched': bool(hit),
                     'hit': hit, 'in_jd': in_jd, 'weak': group_weak})

    total = sum(r['weight'] for r in reqs) or 1.0
    got = sum(r['weight'] for r in reqs if r['matched'])
    score = int(round(100 * got / total)) if resume else 0
    score = max(3, min(97, score)) if resume else 0

    matched = []
    for r in reqs:
        matched.extend(r['hit'])
    matched = sorted(dict.fromkeys(matched), key=lambda s: 0 if s in jd_skills else 1)

    missing_items = []
    for r in reqs:
        if r['matched']:
            continue
        names = [n for n in r['names'] if n in SKILL_INDEX]
        if not names:
            continue
        pick = next((n for n in names if n in jd_skills), names[0])
        missing_items.append({
            'name': pick, 'weight': r['weight'], 'in_jd': pick in jd_skills,
            'group': names, 'label': ' / '.join(names),
        })
    missing_items.sort(key=lambda x: (-x['weight'], -jd_skills.get(x['name'], 0)))

    return {
        'role': role,
        'score': score,
        'matched': matched,
        'missing': [x['name'] for x in missing_items],
        'missing_items': missing_items,
        'jd_skills': jd_skills,
        'resume_skills': resume_skills,
        'core': role['core'],
        'bonus': role['bonus'],
        'required': [' / '.join(r['names']) for r in reqs],
        'reqs': reqs,
        'weights': {n: r['weight'] for r in reqs for n in r['names']},
    }


# ---------------------------------------------------------------------------
# 本地报告
# ---------------------------------------------------------------------------
def _bullet(lines, empty='（招聘页未提供，建议到公司官网 / 招聘主页补齐）'):
    lines = [x for x in lines if x]
    if not lines:
        return f'- {empty}'
    return '\n'.join('- ' + x for x in lines)


def _stage_text(text):
    stage, note = _find_note(text, STAGE_NOTES)
    return stage, note


def _size_text(text):
    size, note = _find_note(text, SIZE_NOTES)
    return size, note


def _intro_summary(intro):
    if not intro:
        return ''
    sentences = [s.strip() for s in re.split(r'(?<=[。！；;])', intro) if len(s.strip()) > 8]
    buf = ''
    for sentence in sentences[:4]:
        if len(buf) + len(sentence) > 260:
            break
        buf += sentence
    buf = buf.strip()
    if buf and not buf.endswith(('。', '！', '；', ';')):
        buf += '。'
    return buf


def _pick_learn_items(match, limit=6):
    """按优先级挑要补的技能（每个"满足其一"的组只挑一个）。"""
    items = []
    for it in match.get('missing_items') or []:
        name = it['name']
        if name not in SKILL_INDEX:
            continue
        skill = SKILL_INDEX[name]
        items.append({
            'name': name,
            'cat': skill['cat'],
            'learn': skill['learn'],
            'resume': skill['resume'],
            'weight': it['weight'],
            'in_jd': it['in_jd'],
            'label': it['label'],
        })
        if len(items) >= limit:
            break
    return items


def _project_idea(cat):
    return PROJECT_IDEAS.get(cat) or PROJECT_IDEAS['通用']


def build_local_report(job, company, resume, profile=None):
    """生成本地规则版 Markdown 报告，返回 (markdown, meta)。"""
    profile = profile or {}
    match = match_resume(job, company, resume)
    role = match['role']
    issues, stats = diagnose_resume(resume)
    title = job.get('title') or '未识别岗位'
    cname = company.get('name') or '目标公司'

    stage_key, stage_note = _stage_text((company.get('stage') or '') + ' ' + (company.get('intro') or ''))
    size_key, size_note = _size_text((company.get('size') or '') + ' ' + (company.get('intro') or ''))
    industry = company.get('industry') or ''
    ind_note = INDUSTRY_NOTES.get(industry, '')

    lines = []
    lines.append(f'# {cname}｜{title} 岗位分析报告')
    lines.append('')
    lines.append(f'> 生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　分析引擎：本地规则（关键词 + 岗位能力模型）')
    lines.append('> 本报告未联网核实工商 / 融资 / 营收数据，公司结论基于招聘页原文，请结合下方尽调清单自行确认。')
    lines.append('')

    # ---------------- 一、公司背景 ----------------
    lines.append('## 一、公司背景')
    lines.append('')
    lines.append('**招聘页能确认的信息**')
    lines.append('')
    info = [f'公司：{cname}']
    if industry:
        info.append(f'行业：{industry}')
    if company.get('size'):
        info.append(f'规模：{company["size"]}')
    if company.get('stage'):
        info.append(f'融资阶段：{company["stage"]}')
    if company.get('founded'):
        info.append(f'成立时间：{company["founded"]}')
    if company.get('reg_capital'):
        info.append(f'注册资本：{company["reg_capital"]}')
    if company.get('insured'):
        info.append(f'参保人数：{company["insured"]}')
    if company.get('address') or job.get('location'):
        info.append(f'地点：{company.get("address") or job.get("location")}')
    if job.get('url'):
        info.append(f'职位链接：{job["url"]}')
    lines.extend('- ' + x for x in info)
    lines.append('')

    summary = _intro_summary(company.get('intro'))
    lines.append('**这家公司在做什么**')
    lines.append('')
    if summary:
        lines.append(f'> 招聘页原文摘要：{summary}')
    else:
        lines.append('> 招聘页没有提供公司简介。面试前至少花 20 分钟补三件事：官网产品介绍、最近一条融资 / 新闻、主营业务的收入来源。')
    lines.append('')

    lines.append('**对你意味着什么**')
    lines.append('')
    if stage_note:
        lines.append(f'- 阶段（{stage_key}）：{stage_note}')
    else:
        lines.append('- 阶段：招聘页未标注融资阶段。直接问 HR「公司目前处在什么阶段、现金流能支撑多久」，不丢人，反而显得理性。')
    if size_note:
        lines.append(f'- 规模（{size_key}）：{size_note}')
    else:
        lines.append('- 规模：未标注人数。面试时问清团队人数、你的直接汇报人和同岗位编制。')
    if ind_note:
        lines.append(f'- 行业（{industry}）：{ind_note}')
    else:
        lines.append('- 行业：招聘页未明确行业，建议从公司官网 / 企查查的「经营范围」确认，再判断业务稳定性。')
    lines.append('')

    # 风险信号
    risks = list(dict.fromkeys(job.get('risk') or []))
    lines.append('**需要核实的信号**')
    lines.append('')
    if risks:
        lines.extend('- ' + r for r in risks[:5])
    else:
        lines.append('- 招聘文本没有明显风险词，但仍建议确认：岗位是新增还是替补、试用期考核、加班与出差频率。')
    lines.append('')

    lines.append('**面试前 30 分钟尽调清单**')
    lines.append('')
    lines.append('- 工商与风险：企查查 / 天眼查看注册资本、成立时间、参保人数、司法与被执行记录')
    lines.append('- 融资与股东：IT 桔子、公司官网「新闻」页，确认最近一轮时间和投资方')
    lines.append('- 口碑：脉脉 / 看准网搜公司名 + 「加班 / 裁员 / 绩效」，注意样本偏差，只当参考')
    lines.append('- 产品：注册体验一次，准备 2 条真实使用感受和 1 条改进建议，面试时非常加分')
    lines.append('- 招聘节奏：同一岗位是否长期挂、是否多个 HR 重复发布，能侧面反映流动率')
    lines.append('')

    # ---------------- 二、岗位画像 ----------------
    lines.append('## 二、岗位画像')
    lines.append('')
    basic = []
    if job.get('salary'):
        basic.append(f'薪资：{job["salary"]}')
    if job.get('exp'):
        basic.append(f'经验：{job["exp"]}')
    if job.get('edu'):
        basic.append(f'学历：{job["edu"]}')
    if job.get('location'):
        basic.append(f'地点：{job["location"]}')
    lines.append('**基本信息**：' + ('　|　'.join(basic) if basic else '招聘页未写全，面试时确认'))
    lines.append('')
    if profile.get('expect'):
        lines.append(f'**你的求职意向**：{profile["expect"]}。如果和这个岗位方向不一致，先想清楚为什么要投——'
                     '面试大概率会问，简历也要提前准备"可迁移能力"的说法。')
        lines.append('')

    if job.get('requirements'):
        lines.append('**JD 明确要求（原文摘录）**')
        lines.append('')
        lines.extend('- ' + x[:150] for x in job['requirements'][:8])
        lines.append('')

    jd_skill_names = sorted(match['jd_skills'], key=lambda s: -match['jd_skills'][s])[:12]
    core_names = set(role['core']) | {n for g in _alt_groups(role) for n in g}
    standard = []
    for req in match['reqs']:
        if req['in_jd'] or not (set(req['names']) & core_names):
            continue
        label = ' / '.join(req['names'])
        if label not in standard:
            standard.append(label)
    lines.append('**技能要求拆解**')
    lines.append('')
    if jd_skill_names:
        lines.append('- JD 点名：' + '、'.join(jd_skill_names))
    if standard:
        lines.append('- 岗位标配（JD 没写但行业默认）：' + '、'.join(standard[:8]))
    if role['bonus']:
        lines.append('- 加分项：' + '、'.join(role['bonus']))
    if not jd_skill_names and not standard:
        lines.append('- JD 里没有识别到明确技能词，建议直接看「岗位职责」前三条判断真实要求。')
    lines.append('')

    lines.append('**这份工作真实在做什么**')
    lines.append('')
    lines.append(f'- {role["work"]}')
    if job.get('responsibilities'):
        lines.append('- JD 里的日常动作：' + '；'.join(x.rstrip('；;。，,')[:60] for x in job['responsibilities'][:3]))
    lines.append('')

    lines.append('**面试会重点考什么**')
    lines.append('')
    lines.append(f'- {role["focus"]}')
    lines.append(f'- 准备路径：{role["path"]}')
    lines.append('')

    # ---------------- 三、匹配度 ----------------
    lines.append('## 三、你和这个岗位的匹配度')
    lines.append('')
    if not clean_text(resume):
        lines.append('**尚未填写简历**：把简历正文（或技能清单 + 项目要点）粘贴到左侧「我的简历」，再点一次分析，才能算匹配度和差距。')
        lines.append('')
    else:
        lines.append(f'**技能关键词匹配度：{match["score"]} / 100**　（只按简历与 JD 的技能词重合计算，不代表真实能力，仅用于找差距）')
        lines.append('')
        if match['matched']:
            lines.append('- 简历已覆盖：' + '、'.join(match['matched'][:10]))
        else:
            lines.append('- 简历里没有命中 JD 的任何技能词：要么确实不匹配，要么是简历表达太笼统，需要把技能名写出来。')
        if match['missing_items']:
            lines.append('- 明显缺口（按岗位重要度排序）：'
                         + '、'.join(x['label'] for x in match['missing_items'][:10]))
        lines.append(f'- 简历文字量：约 {stats["chars"]} 字；可量化数字：{stats["numbers"]} 处；技能词：{stats["skills"]} 个')
        lines.append('')

        if issues:
            lines.append('**简历诊断**')
            lines.append('')
            lines.extend('- ' + x for x in issues[:6])
            lines.append('')

    # ---------------- 四、简历怎么改 ----------------
    lines.append('## 四、简历怎么改（针对这个岗位）')
    lines.append('')
    top_missing = _pick_learn_items(match, 3)
    lines.append(f'1. **开头 3 行对齐岗位**：写成「{job.get("exp") or "X 年"}{role["name"]}经验，做过 {industry or "相关行业"} 的 {job.get("title") or "同类岗位"}，熟悉 '
                 + ('、'.join(match['matched'][:3]) if match['matched'] else '（填 2~3 个真实擅长的技能）')
                 + '，目标岗位：' + title + '」。HR 6 秒内就能判断你对口。')
    lines.append('2. **换掉第一条项目**：把最接近这个 JD 的项目放第一位，按「背景 → 你的动作 → 结果」写 3~4 行，动作里自然带上 JD 的技能词；与岗位无关的项目压到 1 行。')
    if top_missing:
        names = '、'.join(x['name'] for x in top_missing)
        lines.append(f'3. **补关键词（前提是真会）**：{names} 是这份 JD 的高频要求，简历里没有出现。不会就别硬写，先按第五部分做练手项目，再把项目写进简历。')
    else:
        lines.append('3. **补关键词**：JD 主要技能你基本都覆盖了，重点是把「熟练 / 了解」改成「用 X 完成了 Y，结果是 Z」。')
    lines.append('4. **给结果加数字**：每个项目至少 1 个可验证指标（QPS / 耗时 / 成本 / 转化率 / 人数 / 金额 / 缺陷率），没有精确数据就用合理区间，但要能解释口径。')
    lines.append('5. **删干扰项**：与岗位无关的课程、证书、社团经历各留 1 行；照片、籍贯、婚姻状况等非必要信息可以去掉。')
    lines.append('6. **格式**：一页 PDF，文件名「姓名-岗位-年限」，时间倒序，技能清单放在前 1/3，方便 HR 和系统筛选。')
    lines.append('')
    lines.append('**可以直接套用的表达模板**')
    lines.append('')
    team_skill = (match['matched'][0] if match['matched'] else (role['core'][0] if role['core'] else '相关技能'))
    lines.append(f'- 负责 {industry or "业务"}{role["name"]}相关模块，使用 {team_skill} 解决（写清具体问题），（指标）从 A 提升到 B。')
    lines.append(f'- 主导（项目名）从 0 到 1 落地，覆盖（用户量 / 数据量 / 订单量），上线后（指标）改善 X%。')
    lines.append('- 排查并解决（线上问题），定位到（根因），通过（动作）把（指标）降低 X%，避免（损失）。')
    lines.append('> 把括号里的内容换成你的真实经历和数字，不要照抄。')
    lines.append('')

    # ---------------- 五、学习计划 ----------------
    lines.append('## 五、技能学习计划')
    lines.append('')
    learn_items = _pick_learn_items(match, 6)
    if not clean_text(resume):
        lines.append('先填简历，才能算出你缺什么；下面是这个岗位的通用准备顺序。')
        lines.append('')
        learn_items = [{'name': s, 'cat': SKILL_INDEX[s]['cat'], 'learn': SKILL_INDEX[s]['learn'],
                        'resume': SKILL_INDEX[s]['resume'], 'weight': 1, 'in_jd': True,
                        'label': s}
                       for s in role['core'][:4] if s in SKILL_INDEX]
    if learn_items:
        p0 = [x for x in learn_items if x['weight'] >= 2][:3] or learn_items[:2]
        p1 = [x for x in learn_items if x not in p0][:3]
        lines.append('**P0｜1~2 周内补，直接影响面试通过率**')
        lines.append('')
        for item in p0:
            lines.append(f'- **{item["name"]}**（{item["cat"]}）：{item["learn"]}')
            lines.append('  - 验收：能在练手项目里用上它，并准备一个「为什么用 / 怎么用 / 效果数据」的 2 分钟讲述。')
        lines.append('')
        if p1:
            lines.append('**P1｜1 个月内补，决定你能不能聊深**')
            lines.append('')
            for item in p1:
                lines.append(f'- **{item["name"]}**：{item["learn"]}')
                lines.append(f'  - 写进简历时：{item["resume"]}')
            lines.append('')
    lines.append('**30 / 60 / 90 天节奏**')
    lines.append('')
    lines.append(f'- 第 1~30 天：补 P0 技能各做一个最小可运行 demo，同时按第四部分改完简历，开始投递。')
    idea = _project_idea(ROLE_PROJECT_CAT.get(role["name"], "通用")).rstrip('。')
    lines.append(f'- 第 31~60 天：把 demo 合成一个完整项目（参考：{idea}），并准备 3 个面试故事（难点 / 取舍 / 结果）。')
    lines.append('- 第 61~90 天：按目标公司 JD 做定向补强，模拟面试 5 次，复盘每次答不上的问题。')
    lines.append('')
    lines.append('> 学习顺序原则：先补 JD 反复出现且面试必问的，再补岗位标配，最后补加分项。不要从"最难的"开始。')
    lines.append('')

    # ---------------- 六、面试问题 ----------------
    lines.append('## 六、面试可以反问的问题')
    lines.append('')
    questions = []
    questions.append('这个岗位是新增编制还是替补？如果是替补，前任主要卡在什么地方？')
    if not company.get('stage'):
        questions.append('公司目前的融资 / 盈利情况和现金流规划是怎样的？')
    if not company.get('size'):
        questions.append('团队现在多少人？这个岗位的直接汇报人是谁？')
    if job.get('salary'):
        questions.append('薪资结构是怎样的（底薪 / 绩效 / 年终奖），试用期比例和考核标准是什么？')
    if '加班' in (job.get('jd_text') or '') or any('强度' in r for r in risks):
        questions.append('团队平时的作息和加班频率大概是怎样？忙季集中在什么时候？')
    questions.append(f'入职后前 3 个月，您希望我先交付什么结果？考核指标怎么定？')
    questions.append(f'团队目前在{role["name"]}方向上最大的挑战是什么？')
    questions.append('这个岗位的晋升路径和调薪周期是怎样的？')
    lines.extend(f'{i}. {q}' for i, q in enumerate(dict.fromkeys(questions), 1))
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('**说明**：本地规则版报告基于关键词与岗位模型，适合快速判断和准备；配置 AI（右上角「AI 设置」）后可获得更贴合这家公司、这份 JD 的深度分析。')
    lines.append('')
    markdown = '\n'.join(lines)

    meta = {
        'company': cname,
        'title': title,
        'role': role['name'],
        'score': match['score'],
        'missing': [x['name'] for x in learn_items],
        'engine': 'local',
    }
    return markdown, meta


# ---------------------------------------------------------------------------
# 大模型（OpenAI 兼容）
# ---------------------------------------------------------------------------
class LLMError(Exception):
    pass


SYSTEM_PROMPT = (
    '你是一位资深猎头兼职业规划顾问，擅长公司尽调、岗位拆解、简历优化和技能差距分析。'
    '你的建议必须具体、可执行、诚实：信息不足时明确说"需核实"，绝不编造公司数据。'
)


def build_prompt(job, company, resume, profile, match):
    resume = (resume or '').strip()
    jd = (job.get('jd_text') or '').strip()
    company_raw = (company.get('intro') or '').strip()

    facts = []
    if company.get('name'):
        facts.append(f'公司名称：{company["name"]}')
    for key, label in (('industry', '行业'), ('size', '规模'), ('stage', '融资阶段'),
                       ('address', '地址'), ('founded', '成立时间'),
                       ('reg_capital', '注册资本'), ('insured', '参保人数')):
        if company.get(key):
            facts.append(f'{label}：{company[key]}')
    if job.get('title'):
        facts.append(f'岗位：{job["title"]}')
    for key, label in (('salary', '薪资'), ('exp', '经验要求'), ('edu', '学历要求'),
                       ('location', '地点')):
        if job.get(key):
            facts.append(f'{label}：{job[key]}')
    if profile.get('expect'):
        facts.append(f'求职意向：{profile["expect"]}')

    local = [
        f'岗位大类：{match["role"]["name"]}',
        'JD 技能词：' + ('、'.join(list(match['jd_skills'])[:15]) or '未识别'),
        '简历已覆盖：' + ('、'.join(match['matched'][:15]) or '无'),
        '缺口：' + ('、'.join(match['missing'][:15]) or '无'),
        f'关键词匹配度：{match["score"]}/100',
    ]

    prompt = f"""请基于下面材料，为求职者做一份「公司 + 岗位 + 简历 + 学习计划」的分析。

【已知信息】
{chr(10).join('- ' + x for x in facts) if facts else '- 招聘页信息较少'}

【公司介绍原文（可能为空）】
{company_raw[:2500] or '（未提供）'}

【职位描述原文】
{jd[:5000] or '（未提供）'}

【求职者简历原文】
{resume[:6000] or '（未填写）'}

【本地初筛结果（仅供参考，可纠正）】
{chr(10).join('- ' + x for x in local)}

【输出要求】
1. 只输出 Markdown，不要开场白、不要结尾客套。
2. 严格按以下 6 个二级标题输出，顺序不要变：
   ## 一、公司背景与行业判断
   ## 二、岗位真实画像
   ## 三、匹配度评估
   ## 四、简历修改清单
   ## 五、技能学习计划（30/60/90 天）
   ## 六、面试问题与尽调清单
3. 公司部分：说明公司大概做什么、商业模式、所处阶段、竞争位置、对求职者的利弊。凡是材料里没有的事实，标注「需核实」，禁止编造融资、营收、人数、客户。
4. 岗位部分：区分 JD 写明的硬要求和没说但实际会考的隐性要求；指出这份工作真实的一天在干什么，以及可能的坑。
5. 匹配度：基于简历逐条对照，给出 0~100 的分数和理由，列 3~5 条优势和 3~5 条差距。简历若未填写，直接说明无法评估。
6. 简历修改：给 5~8 条具体动作，必须包含可直接替换的句子 / bullet 模板，并提醒补真实数字；不要只说"突出亮点"。
7. 学习计划：按 P0/P1/P2 优先级，每项写「学什么 + 怎么验收 + 预计时间」，落到 30/60/90 天节奏，不要罗列课程名。
8. 面试问题：给 6~8 个求职者可以反问的问题，要能帮他判断这家公司值不值得去。
9. 全部用中文，语气直接、务实，不要空话。"""
    return prompt


def _post_json(url, payload, api_key, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'ignore')[:300]
        except Exception:
            pass
        if e.code == 401:
            raise LLMError('API Key 无效或未授权（401），请检查「AI 设置」里的 Key') from e
        if e.code == 402:
            raise LLMError('账户余额不足（402），请先充值或换一个 Key') from e
        if e.code == 404:
            raise LLMError('接口地址或模型名不对（404），检查 Base URL 是否以 /v1 结尾、模型名是否存在') from e
        if e.code == 429:
            raise LLMError('请求太频繁或超出额度（429），稍后再试') from e
        raise LLMError(f'接口返回 HTTP {e.code}：{detail or e.reason}') from e
    except urllib.error.URLError as e:
        raise LLMError(f'连不上接口：{e.reason}（检查网络 / 代理 / Base URL）') from e
    except TimeoutError as e:
        raise LLMError('请求超时，可能是网络慢或内容太长；可稍后重试') from e


def llm_analyze(job, company, resume, profile, match, cfg=None):
    cfg = cfg or load_config()
    api_key = (cfg.get('llm_api_key') or '').strip()
    if not api_key:
        raise LLMError('还没有配置 API Key，请点击「AI 设置」填写')

    base = (cfg.get('llm_base_url') or DEFAULT_CONFIG['llm_base_url']).strip().rstrip('/')
    url = base if base.endswith('/chat/completions') else base + '/chat/completions'
    model = (cfg.get('llm_model') or 'deepseek-chat').strip()

    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': build_prompt(job, company, resume, profile, match)},
        ],
        'temperature': 0.4,
        'max_tokens': int(cfg.get('llm_max_tokens') or 2200),
        'stream': False,
    }
    data = _post_json(url, payload, api_key, int(cfg.get('llm_timeout') or 120))
    try:
        content = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise LLMError('接口返回格式不对：' + json.dumps(data, ensure_ascii=False)[:200])
    content = (content or '').strip()
    if not content:
        raise LLMError('模型返回了空内容，请重试')

    # 去掉代码围栏和模型自己加的标题
    content = re.sub(r'^```(?:markdown|md)?\s*', '', content)
    content = re.sub(r'\s*```$', '', content).strip()
    content = re.sub(r'^#\s+.*\n+', '', content)

    title = job.get('title') or '未识别岗位'
    cname = company.get('name') or '目标公司'
    head = [
        f'# {cname}｜{title} 岗位分析报告（AI）',
        '',
        f'> 生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　分析引擎：{model}',
        '> 公司信息来自招聘页原文，AI 未联网核实；涉及融资 / 营收 / 人数的结论请以工商信息和面试为准。',
        '',
    ]
    return '\n'.join(head) + content + '\n'


def llm_ping(cfg=None):
    """测试连接：返回模型回复的一句话。"""
    cfg = cfg or load_config()
    api_key = (cfg.get('llm_api_key') or '').strip()
    if not api_key:
        raise LLMError('请先填写 API Key')
    base = (cfg.get('llm_base_url') or DEFAULT_CONFIG['llm_base_url']).strip().rstrip('/')
    url = base if base.endswith('/chat/completions') else base + '/chat/completions'
    payload = {
        'model': (cfg.get('llm_model') or 'deepseek-chat').strip(),
        'messages': [{'role': 'user', 'content': '只回复两个字：可用'}],
        'temperature': 0,
        'max_tokens': 16,
        'stream': False,
    }
    data = _post_json(url, payload, api_key, 30)
    try:
        return (data['choices'][0]['message']['content'] or '').strip() or '可用'
    except (KeyError, IndexError, TypeError):
        raise LLMError('接口返回格式不对，请检查 Base URL')


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def analyze(job, company, resume, engine='local', cfg=None, profile=None):
    """返回 {markdown, engine, fallback_reason, meta}。AI 失败会降级为本地报告。"""
    cfg = cfg or load_config()
    profile = profile if profile is not None else load_profile()
    local_md, meta = build_local_report(job, company, resume, profile)
    if engine != 'llm':
        return {'markdown': local_md, 'engine': 'local', 'fallback_reason': '', 'meta': meta}

    match = match_resume(job, company, resume)
    try:
        md = llm_analyze(job, company, resume, profile, match, cfg)
        meta['engine'] = 'llm'
        return {'markdown': md, 'engine': 'llm', 'fallback_reason': '', 'meta': meta}
    except LLMError as e:
        notice = (f'> ⚠️ AI 分析失败：{e}\n> 已自动改用本地规则版报告。\n\n')
        return {'markdown': notice + local_md, 'engine': 'local',
                'fallback_reason': str(e), 'meta': meta}
    except Exception as e:  # 兜底，任何异常都不该让整个任务中断
        notice = (f'> ⚠️ AI 分析异常：{e}\n> 已自动改用本地规则版报告。\n\n')
        return {'markdown': notice + local_md, 'engine': 'local',
                'fallback_reason': str(e), 'meta': meta}


# ---------------------------------------------------------------------------
# 报告落盘
# ---------------------------------------------------------------------------
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_name(name, limit=48):
    name = _BAD_FILENAME.sub('_', (name or '').strip()).strip('_')
    return (name or '报告')[:limit]


def save_report(markdown, company='', title=''):
    os.makedirs(REPORT_DIR, exist_ok=True)
    hint = _safe_name(f'{company}_{title}') if company or title else '岗位分析'
    path = os.path.join(REPORT_DIR, f'{hint}_{datetime.now():%Y%m%d_%H%M%S}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(markdown.rstrip() + '\n')
    return path


class BatchReport:
    """发送前自动分析的汇总报告：边分析边追加，中途停止也不丢数据。"""

    def __init__(self, prefix='批次'):
        os.makedirs(REPORT_DIR, exist_ok=True)
        self.path = os.path.join(REPORT_DIR, f'{prefix}_{datetime.now():%Y%m%d_%H%M%S}.md')
        self.count = 0
        self._write([
            f'# {prefix}：公司与岗位分析',
            '',
            f'> 开始时间：{datetime.now():%Y-%m-%d %H:%M:%S}',
            '> 每个职位一节，按发送顺序追加；公司结论基于招聘页原文，未经工商核实。',
            '',
        ])

    def _write(self, lines):
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

    def add(self, markdown, index=None):
        self.count += 1
        num = index if index is not None else self.count
        body = markdown.strip()
        # 汇总报告里降一级标题，避免目录太碎
        body = re.sub(r'^##\s+', '### ', body, flags=re.M)
        body = re.sub(r'^#\s+', '## ', body, flags=re.M)
        self._write([
            '',
            '---',
            '',
            f'<!-- job {num} -->',
            body,
            '',
        ])

    def finish(self):
        self._write(['', '---', '', f'> 结束时间：{datetime.now():%Y-%m-%d %H:%M:%S}，共分析 {self.count} 个职位。', ''])
        return self.path
