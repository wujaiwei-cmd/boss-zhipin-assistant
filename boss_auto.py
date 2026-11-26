# -*- coding: utf-8 -*-
"""
Boss直聘一键打招呼 + 公司与岗位分析工具

功能：
1. 实时监听本机 Boss直聘 PC 客户端（收到"信号"后自动打开网页版并激活本工具）
2. 用户点击"开始发送"后，自动对职位列表中所有可沟通职位发送问候语
3. 支持按 职位关键词 / 城市（可多选，按顺序逐个城市搜索）/ 公司行业 筛选目标职位
4. 公司与岗位分析：抓取/粘贴职位链接或当前选中的职位，分析公司背景、岗位画像、
   简历匹配度、简历改法和 30/60/90 天学习计划；可保存 Markdown 报告
5. 可选 AI 深度分析：配置任意 OpenAI 兼容接口（默认 DeepSeek）后调用大模型，
   Key 无效或断网时自动降级为本地规则报告
6. 发送前自动分析：每个职位打招呼前生成一份分析，汇总到 reports/ 的批次报告

说明：基于浏览器自动化（DrissionPage），使用独立浏览器配置，登录状态可长期保留。
      页面结构适配 2025+ 新版 Boss直聘网页（列表+详情面板布局）。
"""
import os
import random
import re
import threading
import time
import queue
import traceback
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime
from urllib.parse import quote, urljoin

import psutil

import job_analysis as ja

HOME_URL = 'https://www.zhipin.com/web/user/?ka=header-home'
JOBS_URL = 'https://www.zhipin.com/web/geek/jobs'
DEFAULT_GREETING = '您好，我对这个职位很感兴趣，期待与您进一步沟通。'
BROWSER_PORT = 9333   # 工具托管浏览器的调试端口

# Boss直聘网页版城市代码（可在下拉框中直接输入纯数字自定义代码）
CITY_MAP = {
    '不限': '',
    '全国': '100010000',
    '北京': '101010100', '上海': '101020100', '深圳': '101280600',
    '广州': '101280100', '杭州': '101210100', '成都': '101270100',
    '南京': '101190100', '武汉': '101200100', '西安': '101110100',
    '郑州': '101180100', '长沙': '101250100', '重庆': '101040100',
    '苏州': '101190400', '天津': '101030100', '合肥': '101220100',
    '青岛': '101120200', '济南': '101120100', '宁波': '101210400',
    '厦门': '101230200', '福州': '101230100', '东莞': '101281600',
    '佛山': '101280800', '无锡': '101190200', '昆明': '101290100',
    '南昌': '101240100', '贵阳': '101260100', '南宁': '101300100',
    '石家庄': '101090100', '太原': '101100100', '沈阳': '101070100',
    '大连': '101060200', '哈尔滨': '101050100', '长春': '101060100',
}

# Boss直聘网页版"公司行业"筛选项（2025-09 实测提取）
INDUSTRY_LIST = [
    '互联网', '电子商务', '计算机软件', '生活服务(O2O)', '企业服务', '医疗健康',
    '游戏', '社交网络与媒体', '人工智能', '云计算', '在线教育', '计算机服务',
    '大数据', '广告营销', '物联网', '新零售', '信息安全', '半导体/芯片',
    '电子/硬件开发', '通信/网络设备', '智能硬件/消费电子', '运营商/增值服务',
    '计算机硬件', '电子/半导体/集成电路', '餐饮', '美容', '美发', '酒店/民宿',
    '休闲/娱乐', '运动/健身', '保健/养生', '家政服务', '旅游/景区', '婚庆/摄影',
    '宠物服务', '回收/维修', '其他生活服务', '批发/零售', '进出口贸易',
    '食品/饮料/烟酒', '服装/纺织', '家具/家居', '家用电器', '日化', '珠宝/首饰',
    '家具/家电/家居', '其他消费品', '装修装饰', '房屋建筑工程', '土木工程',
    '机电工程', '物业管理', '房地产中介/租赁', '建筑材料', '房地产开发经营',
    '建筑设计', '建筑工程咨询服务', '土地与公共设施管理', '工程施工',
    '培训/辅导机构', '职业培训', '学前教育', '学校/学历教育', '学术/科研',
    '文化艺术/娱乐', '体育', '广告/公关/会展', '广播/影视', '新闻/出版',
    '通用设备', '专用设备', '电气机械/器材', '金属制品', '非金属矿物制品',
    '橡胶/塑料制品', '化学原料/化学制品', '仪器仪表', '自动化设备',
    '印刷/包装/造纸', '铁路/船舶/航空/航天制造', '计算机/通信/其他电子设备',
    '新材料', '机械设备/机电/重工', '仪器仪表/工业自动化', '原材料及加工/模具',
    '其他制造业', '咨询', '财务/审计/税务', '人力资源服务', '法律',
    '检测/认证/知识产权', '翻译', '其他专业服务', '医疗服务', '医美服务',
    '医疗器械', 'IVD', '生物/制药', '医药批发零售', '医疗研发外包',
    '新能源汽车', '汽车智能网联', '汽车经销商', '汽车后市场', '汽车研发/制造',
    '汽车零部件', '摩托车/自行车制造', '4S店/后市场', '即时配送', '快递',
    '公路物流', '同城货运', '跨境物流', '装卸搬运和仓储业', '客运服务',
    '港口/铁路/公路/机场', '交通/运输', '物流/仓储', '光伏', '储能',
    '动力电池', '风电', '其他新能源', '环保', '化工', '电力/热力/燃气/水利',
    '石油/石化', '矿产/地质', '采掘/冶炼', '新能源', '互联网金融', '银行',
    '投资/融资', '证券/期货', '基金', '保险', '租赁/拍卖/典当/担保', '信托',
    '财富管理', '其他金融业', '农/林/牧/渔', '非营利组织', '政府/公共事业',
    '其他行业',
]


def find_browser_path():
    """查找本机 Chromium 内核浏览器（Chrome / Edge）"""
    candidates = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


class App:
    def __init__(self, root):
        self.root = root
        root.title('Boss直聘一键打招呼 + 公司与岗位分析')
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        win_w, win_h = min(1100, max(960, sw - 120)), min(780, max(680, sh - 160))
        root.geometry(f'{win_w}x{win_h}+{max(0, (sw - win_w) // 2)}+{max(20, (sh - win_h) // 2 - 30)}')
        root.minsize(min(960, win_w), min(680, win_h))

        self.msg_q = queue.Queue()

        # 运行状态
        self.page = None                 # DrissionPage 页面对象
        self.browser_lock = threading.Lock()
        self.browser_start_lock = threading.Lock()
        self.browser_launched = False    # 浏览器是否由本工具启动（决定退出时是否关闭）
        self.stop_all = threading.Event()   # 程序级停止（监控线程）
        self.stop_run = threading.Event()   # 停止当前发送任务
        self.monitor_thread = None
        self.send_thread = None

        # 检测状态缓存
        self.state_pc = False
        self.state_web = False
        self.auto_open = True   # 普通布尔值，供监听线程安全读取

        self.processed = set()           # 已处理职位集合
        self.selected_cities = ['不限']   # 多选城市（空/不限 = 不传 city 参数）

        # 分析与配置状态
        self.cfg = ja.load_config()
        self.profile = ja.load_profile()
        self.analysis_thread = None
        self.analysis_busy = False
        self.last_report_md = ''
        self.last_report_meta = {}
        self.last_report_path = ''
        self.batch_report = None         # 发送前自动分析的批次报告
        self.resume_for_run = ''         # on_start 时快照，避免发送线程读 Tk 控件
        self.cfg_snapshot = dict(self.cfg)

        self._build_ui()

        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()

        root.protocol('WM_DELETE_WINDOW', self._on_close)
        root.after(150, self._poll)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        try:
            ttk.Style().configure('TNotebook.Tab', padding=(16, 6))
        except Exception:
            pass
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True)
        self.tab_send = ttk.Frame(self.nb)
        self.tab_analysis = ttk.Frame(self.nb)
        self.nb.add(self.tab_send, text='  一键打招呼  ')
        self.nb.add(self.tab_analysis, text='  公司与岗位分析  ')
        self._build_send_tab(self.tab_send)
        self._build_analysis_tab(self.tab_analysis)

    def _build_send_tab(self, main):
        pad = dict(padx=10, pady=4)

        # 状态区
        status = ttk.LabelFrame(main, text='Boss直聘信号状态')
        status.pack(fill='x', **pad)
        row = ttk.Frame(status)
        row.pack(fill='x', padx=10, pady=6)
        ttk.Label(row, text='PC客户端：').pack(side='left')
        self.lbl_pc = ttk.Label(row, text='未检测到', foreground='#999999')
        self.lbl_pc.pack(side='left', padx=(0, 20))
        ttk.Label(row, text='网页版：').pack(side='left')
        self.lbl_web = ttk.Label(row, text='未连接', foreground='#999999')
        self.lbl_web.pack(side='left', padx=(0, 20))
        self.chk_auto_open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text='检测到Boss直聘后自动打开网页版',
                        variable=self.chk_auto_open_var,
                        command=self._sync_auto_open).pack(side='right')

        # 筛选区
        flt = ttk.LabelFrame(main, text='职位筛选（可选）')
        flt.pack(fill='x', **pad)
        rowf1 = ttk.Frame(flt)
        rowf1.pack(fill='x', padx=10, pady=(6, 2))
        ttk.Label(rowf1, text='职位关键词：').pack(side='left')
        self.ent_keyword = ttk.Entry(rowf1, width=18)
        self.ent_keyword.pack(side='left', padx=(0, 16))
        ttk.Label(rowf1, text='城市：').pack(side='left')
        self.var_city_label = tk.StringVar(value=self._city_summary(self.selected_cities))
        self.lbl_city = ttk.Label(rowf1, textvariable=self.var_city_label, width=14,
                                  relief='sunken', anchor='w', padding=(4, 2))
        self.lbl_city.configure(cursor='hand2')
        self.lbl_city.bind('<Button-1>', lambda e: self.on_pick_cities())
        self.lbl_city.pack(side='left')
        self.btn_city = ttk.Button(rowf1, text='选择…', width=7,
                                   command=self.on_pick_cities)
        self.btn_city.pack(side='left', padx=(4, 6))
        ttk.Label(rowf1, text='（可多选：按顺序逐城搜索，额度均分）',
                  foreground='#999999').pack(side='left', padx=(0, 12))
        rowf2 = ttk.Frame(flt)
        rowf2.pack(fill='x', padx=10, pady=(2, 8))
        ttk.Label(rowf2, text='公司行业：').pack(side='left')
        self.cmb_industry = ttk.Combobox(rowf2, width=24,
                                         values=['不限'] + INDUSTRY_LIST)
        self.cmb_industry.set('不限')
        self.cmb_industry.pack(side='left', padx=(0, 8))
        ttk.Label(rowf2, foreground='#777777',
                  text='三项均留空时，发送工具浏览器当前打开的列表').pack(side='left')

        # 设置区
        setting = ttk.LabelFrame(main, text='发送设置')
        setting.pack(fill='x', **pad)
        ttk.Label(setting, text='补充介绍（可选）：打招呼后自动补发的一条消息；留空则只发送Boss直聘中设置的打招呼语：').pack(anchor='w', padx=10, pady=(6, 0))
        self.txt_greeting = scrolledtext.ScrolledText(setting, height=3, font=('Microsoft YaHei UI', 10))
        self.txt_greeting.pack(fill='x', padx=10, pady=4)
        self.txt_greeting.insert('1.0', DEFAULT_GREETING)

        row2 = ttk.Frame(setting)
        row2.pack(fill='x', padx=10, pady=(0, 8))
        ttk.Label(row2, text='发送上限：').pack(side='left')
        self.spin_max = ttk.Spinbox(row2, from_=1, to=200, width=6)
        self.spin_max.set(30)
        self.spin_max.pack(side='left', padx=(0, 16))
        ttk.Label(row2, text='发送间隔（秒）：').pack(side='left')
        self.spin_dmin = ttk.Spinbox(row2, from_=3, to=120, width=5)
        self.spin_dmin.set(8)
        self.spin_dmin.pack(side='left')
        ttk.Label(row2, text='~').pack(side='left')
        self.spin_dmax = ttk.Spinbox(row2, from_=3, to=120, width=5)
        self.spin_dmax.set(15)
        self.spin_dmax.pack(side='left')

        row3 = ttk.Frame(setting)
        row3.pack(fill='x', padx=10, pady=(0, 4))
        self.var_auto_analyze = tk.BooleanVar(value=bool(self.cfg.get('auto_analyze', True)))
        ttk.Checkbutton(row3, text='发送前自动分析该职位，写入批次报告',
                        variable=self.var_auto_analyze,
                        command=self._save_send_options).pack(side='left')
        ttk.Label(row3, text='引擎：').pack(side='left', padx=(14, 2))
        self.cmb_auto_engine = ttk.Combobox(row3, width=12, state='readonly',
                                            values=['本地规则', 'AI 深度分析'])
        self.cmb_auto_engine.set('AI 深度分析' if self.cfg.get('auto_engine') == 'llm' else '本地规则')
        self.cmb_auto_engine.pack(side='left')
        self.cmb_auto_engine.bind('<<ComboboxSelected>>', lambda e: self._save_send_options())
        ttk.Label(setting, foreground='#777777',
                  text='自动分析只用列表面板里的公司信息（几秒一个，不额外开标签页）；AI 引擎每个职位约 10~30 秒，会消耗额度。'
                  ).pack(anchor='w', padx=10, pady=(0, 8))

        # 控制区
        ctrl = ttk.Frame(main)
        ctrl.pack(fill='x', **pad)
        self.btn_open = ttk.Button(ctrl, text='打开网页版', command=self.on_open_browser, width=14)
        self.btn_open.pack(side='left', padx=(0, 10))
        self.btn_start = ttk.Button(ctrl, text='开始发送', command=self.on_start, width=14)
        self.btn_start.pack(side='left', padx=(0, 10))
        self.btn_stop = ttk.Button(ctrl, text='停止', command=self.on_stop, width=14, state='disabled')
        self.btn_stop.pack(side='left')
        self.lbl_progress = ttk.Label(ctrl, text='已发送：0')
        self.lbl_progress.pack(side='right')

        # 日志区
        logf = ttk.LabelFrame(main, text='运行日志')
        logf.pack(fill='both', expand=True, **pad)
        self.txt_log = scrolledtext.ScrolledText(logf, height=12, state='disabled',
                                                 font=('Consolas', 9), wrap='word')
        self.txt_log.pack(fill='both', expand=True, padx=6, pady=6)

        tip = ttk.Label(main, foreground='#777777',
                        text='提示：点击"立即沟通"时平台自动发送账号设置的打招呼语（在Boss直聘【消息通知-设置招呼语】中修改）。'
                             '请使用本工具打开的浏览器窗口；发送频率过高可能触发风控或账号限制。')
        tip.pack(fill='x', padx=12, pady=(0, 8))

    # ------------------------------------------------------------ 分析标签页
    def _city_summary(self, names):
        names = [n for n in (names or []) if n]
        if not names or names == ['不限']:
            return '不限'
        if len(names) == 1:
            return names[0]
        if len(names) <= 3:
            return '、'.join(names)
        return '、'.join(names[:2]) + f' 等{len(names)}个'

    def on_pick_cities(self):
        if self.send_thread is not None and self.send_thread.is_alive():
            messagebox.showinfo('请稍等', '正在发送，等当前任务结束再改城市。')
            return
        CityPickerDialog(self.root, self.selected_cities, self._apply_city_selection)

    def _apply_city_selection(self, names):
        self.selected_cities = list(names) or ['不限']
        self.var_city_label.set(self._city_summary(self.selected_cities))
        shown = '、'.join(self.selected_cities[:8])
        if len(self.selected_cities) > 8:
            shown += f' 等{len(self.selected_cities)}个'
        self.log(f'已选城市（{len(self.selected_cities)}）：{shown}')

    def _save_send_options(self):
        try:
            self.cfg['auto_analyze'] = bool(self.var_auto_analyze.get())
            self.cfg['auto_engine'] = ('llm' if self.cmb_auto_engine.get().startswith('AI')
                                       else 'local')
            ja.save_config(self.cfg)
        except Exception:
            pass

    def _build_analysis_tab(self, parent):
        parent.grid_columnconfigure(0, weight=0, minsize=470)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky='nsew', padx=(10, 5), pady=8)
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky='nsew', padx=(5, 10), pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # ---- 简历 ----
        rf = ttk.LabelFrame(left, text='我的简历 / 技能清单（只保存在本机 profile.json）')
        rf.pack(fill='x', pady=(0, 8))
        ttk.Label(rf, foreground='#777777',
                  text='粘贴简历正文，或至少写清：年限、项目、技能、量化结果。分析匹配度和学习计划都靠它。'
                  ).pack(anchor='w', padx=8, pady=(6, 0))
        self.txt_resume = scrolledtext.ScrolledText(
            rf, height=8, wrap='word', font=('Microsoft YaHei UI', 9))
        self.txt_resume.pack(fill='x', padx=8, pady=4)
        if self.profile.get('resume'):
            self.txt_resume.insert('1.0', self.profile['resume'])
        row = ttk.Frame(rf)
        row.pack(fill='x', padx=8, pady=(0, 8))
        ttk.Label(row, text='求职意向：').pack(side='left')
        self.ent_expect = ttk.Entry(row, width=24)
        self.ent_expect.pack(side='left', padx=(0, 8), fill='x', expand=True)
        if self.profile.get('expect'):
            self.ent_expect.insert(0, self.profile['expect'])
        ttk.Button(row, text='保存', width=8, command=self.on_save_profile).pack(side='right')

        # ---- 职位来源 ----
        sf = ttk.LabelFrame(left, text='职位来源（三种任选其一）')
        sf.pack(fill='both', expand=True)
        ttk.Label(sf, text='① 粘贴 Boss 职位链接：').pack(anchor='w', padx=8, pady=(6, 2))
        row1 = ttk.Frame(sf)
        row1.pack(fill='x', padx=8)
        self.ent_job_url = ttk.Entry(row1)
        self.ent_job_url.pack(side='left', fill='x', expand=True)
        self.btn_fetch = ttk.Button(row1, text='抓取并分析', width=12,
                                    command=self.on_analyze_link)
        self.btn_fetch.pack(side='left', padx=(6, 0))

        row2 = ttk.Frame(sf)
        row2.pack(fill='x', padx=8, pady=(8, 2))
        ttk.Label(row2, text='② 分析浏览器里当前点开的职位：').pack(side='left')
        self.btn_current = ttk.Button(row2, text='分析当前职位', width=12,
                                      command=self.on_analyze_current)
        self.btn_current.pack(side='right')

        ttk.Label(sf, text='③ 抓取失败时，可直接粘贴 JD / 公司介绍：').pack(anchor='w', padx=8, pady=(8, 2))
        self.txt_paste = scrolledtext.ScrolledText(
            sf, height=4, wrap='word', font=('Microsoft YaHei UI', 9))
        self.txt_paste.pack(fill='both', expand=True, padx=8)
        row3 = ttk.Frame(sf)
        row3.pack(fill='x', padx=8, pady=(4, 8))
        ttk.Label(row3, text='分析引擎：').pack(side='left')
        self.cmb_engine = ttk.Combobox(row3, width=14, state='readonly',
                                       values=['本地规则', 'AI 深度分析'])
        self.cmb_engine.set('AI 深度分析' if self.cfg.get('manual_engine') == 'llm' else '本地规则')
        self.cmb_engine.pack(side='left')
        self.btn_paste = ttk.Button(row3, text='分析粘贴内容', width=12,
                                    command=self.on_analyze_paste)
        self.btn_paste.pack(side='left', padx=(6, 0))
        self.btn_ai = ttk.Button(row3, text='AI 设置…', width=9, command=self.on_ai_settings)
        self.btn_ai.pack(side='right')
        self.lbl_ai_state = ttk.Label(sf, foreground='#777777', text='')
        self.lbl_ai_state.pack(anchor='w', padx=8, pady=(0, 6))
        self.analysis_buttons = [self.btn_fetch, self.btn_current, self.btn_paste]
        self._refresh_ai_state()

        # ---- 结果区 ----
        bar = ttk.Frame(right)
        bar.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        self.btn_copy_report = ttk.Button(bar, text='复制全文', width=9,
                                          state='disabled', command=self.on_copy_report)
        self.btn_copy_report.pack(side='right', padx=(4, 0))
        self.btn_open_reports = ttk.Button(bar, text='打开报告目录', width=12,
                                           command=self.on_open_reports)
        self.btn_open_reports.pack(side='right', padx=(4, 0))
        self.btn_save_report = ttk.Button(bar, text='另存一份', width=10,
                                          state='disabled', command=self.on_save_report)
        self.btn_save_report.pack(side='right')
        self.lbl_analysis = ttk.Label(bar, text='等待分析…', foreground='#777777', anchor='w')
        self.lbl_analysis.pack(side='left', fill='x', expand=True)

        self.txt_result = scrolledtext.ScrolledText(
            right, state='disabled', wrap='char', font=('Microsoft YaHei UI', 9))
        self.txt_result.grid(row=1, column=0, sticky='nsew')
        self._init_result_tags()
        self._show_result_placeholder()

    def _init_result_tags(self):
        w = self.txt_result
        w.tag_configure('h1', font=('Microsoft YaHei UI', 13, 'bold'),
                        foreground='#123a63', spacing1=12, spacing3=6)
        w.tag_configure('h2', font=('Microsoft YaHei UI', 11, 'bold'),
                        foreground='#0b5a3c', spacing1=10, spacing3=4)
        w.tag_configure('h3', font=('Microsoft YaHei UI', 10, 'bold'),
                        foreground='#333333', spacing1=6, spacing3=2)
        w.tag_configure('bold', font=('Microsoft YaHei UI', 9, 'bold'))
        w.tag_configure('quote', foreground='#666666', lmargin1=14, lmargin2=14)
        w.tag_configure('bullet', lmargin1=14, lmargin2=30)
        w.tag_configure('rule', foreground='#bbbbbb')
        w.tag_configure('warn', foreground='#a15c00')

    def _show_result_placeholder(self):
        self.txt_result.config(state='normal')
        self.txt_result.delete('1.0', 'end')
        self.txt_result.insert('end', '这里会显示分析报告。\n\n'
                                      '流程：先在左边填好简历 → 粘贴职位链接（或分析当前点开的职位）→ 点分析。\n'
                                      '没有 AI Key 也能用：本地规则版会给出公司解读、岗位画像、匹配度、简历改法和学习计划。',
                               'quote')
        self.txt_result.config(state='disabled')

    def _refresh_ai_state(self):
        cfg = self.cfg or ja.load_config()
        if ja.has_api_key(cfg):
            self.lbl_ai_state.config(
                text=f'AI 已配置：{cfg.get("llm_model", "deepseek-chat")}（分析时调用）',
                foreground='#1a7f37')
        else:
            self.lbl_ai_state.config(
                text='AI 未配置：本地规则照常可用，点「AI 设置…」可启用深度分析',
                foreground='#a15c00')

    def on_ai_settings(self):
        AISettingsDialog(self)

    def _resume_text(self):
        return self.txt_resume.get('1.0', 'end').strip()

    def on_save_profile(self):
        try:
            self.profile = ja.save_profile(self._resume_text(), self.ent_expect.get().strip())
            self.lbl_analysis.config(text='简历已保存到本机 profile.json', foreground='#1a7f37')
            self.log('简历已保存（仅本机）')
        except Exception as e:
            self.report_error('保存简历失败', e)

    # ------------------------------------------------------------ 分析流程
    def _engine_choice(self):
        return 'llm' if self.cmb_engine.get().startswith('AI') else 'local'

    def on_analyze_link(self):
        url = self.ent_job_url.get().strip()
        if not url:
            messagebox.showinfo('提示', '请先粘贴 Boss 直聘职位链接')
            return
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        if 'zhipin.com' not in url:
            if not messagebox.askyesno('确认', '这个链接不是 zhipin.com，仍然尝试抓取吗？'):
                return
        self._start_analysis('link', url=url)

    def on_analyze_current(self):
        self._start_analysis('current')

    def on_analyze_paste(self):
        text = self.txt_paste.get('1.0', 'end').strip()
        if len(text) < 30:
            messagebox.showinfo('提示', '请先粘贴 JD 或公司介绍（至少 30 字）')
            return
        self._start_analysis('paste', pasted=text)

    def _start_analysis(self, source, url='', pasted=''):
        if self.analysis_busy:
            return
        if self.send_thread is not None and self.send_thread.is_alive():
            messagebox.showinfo('请稍等', '正在发送消息，等当前任务结束后再分析，避免抢浏览器。')
            return
        resume = self._resume_text()
        if not resume and not messagebox.askyesno(
                '还没填简历', '没有简历就无法计算匹配度和技能差距。\n只分析公司和岗位，继续吗？'):
            return
        self.on_save_profile()
        self._set_analysis_busy(True)
        self.lbl_analysis.config(text='正在抓取 / 分析，请稍候…', foreground='#0b5a3c')
        label = {'link': '职位链接', 'current': '当前职位', 'paste': '粘贴内容'}.get(source, source)
        self._append_log(f'开始分析（{label}）')
        self.analysis_thread = threading.Thread(
            target=self._analysis_worker,
            args=(source, url, pasted, resume, dict(self.cfg), self._engine_choice()),
            daemon=True)
        self.analysis_thread.start()

    def _analysis_worker(self, source, url, pasted, resume, cfg, engine):
        try:
            self.msg_q.put(('analysis_status', '正在打开职位页…' if source == 'link'
                            else '正在读取页面…'))
            if source == 'link':
                job, company = self._collect_from_url(url)
            elif source == 'current':
                job, company = self._collect_from_current()
            else:
                job, company = self._collect_from_text(pasted)
            if not job.get('title'):
                job['title'] = '未识别岗位'
            self.msg_q.put(('analysis_status', '正在生成报告…'))
            profile = {'resume': resume, 'expect': self.profile.get('expect', '')}
            result = ja.analyze(job, company, resume, engine=engine, cfg=cfg, profile=profile)
            try:
                path = ja.save_report(result['markdown'],
                                      (result.get('meta') or {}).get('company', ''),
                                      (result.get('meta') or {}).get('title', ''))
            except Exception as save_err:
                path = ''
                self.msg_q.put(('analysis_status', f'报告已生成，但保存失败：{save_err}'))
            self.msg_q.put(('analysis_done', (result['markdown'], result, path)))
        except Exception as e:
            self.report_error('分析失败', e)
            self.msg_q.put(('analysis_error', str(e)))

    # --------------------------------------------------------- 抓取与解析
    def _wait_tab_ready(self, tab, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                url = tab.url or ''
                if 'security-check' in url:
                    return 'verify'
                if '/web/user' in url or 'login' in url.lower():
                    return 'login'
                body = tab.ele('tag:body', timeout=1)
                text = (body.text or '') if body else ''
                if 'zhipin.com' in url and len(text) > 200:
                    return 'ok'
            except Exception:
                pass
            time.sleep(0.4)
        return 'timeout'

    def _ele_text(self, scope, selectors, limit=80):
        for loc in selectors:
            try:
                el = scope.ele(loc, timeout=0.6)
                if el:
                    text = re.sub(r'\s+', ' ', (el.text or '')).strip()
                    if text:
                        return text[:limit]
            except Exception:
                continue
        return ''

    def _detail_raw(self, tab):
        for loc in ('css:.job-detail-box', 'css:.job-detail', 'css:.job-detail-content',
                    'css:.job-banner'):
            try:
                el = tab.ele(loc, timeout=1.2)
                if el:
                    text = (el.text or '').strip()
                    if len(text) > 80:
                        return text[:9000]
            except Exception:
                continue
        try:
            body = tab.ele('tag:body', timeout=1)
            return ((body.text or '').strip())[:9000] if body else ''
        except Exception:
            return ''

    def _collect_from_url(self, url):
        with self.browser_lock:
            page = self.page
        if page is None:
            page = self.ensure_browser()
        tab = None
        created = False
        try:
            try:
                tab = page.new_tab(url)
                created = True
            except Exception:
                tab = page
                tab.get(url)
            state = self._wait_tab_ready(tab)
            if state == 'verify':
                raise RuntimeError('Boss 触发了安全验证，请先在浏览器里手动完成验证，再重试。')
            if state == 'login':
                raise RuntimeError('该页面需要登录：请先在工具浏览器里完成 Boss 直聘登录，再点一次分析。')
            if state == 'timeout':
                raise RuntimeError('页面加载超时：可能需要登录，或网络太慢。先在工具浏览器里打开该职位确认能看到内容。')
            raw = self._detail_raw(tab)
            if len(raw) < 80:
                raise RuntimeError('没抓到职位正文，可能页面需要登录；请先在浏览器中登录 Boss 直聘。')

            title = self._ele_text(tab, ['css:.job-banner .name h1', 'css:.job-banner .name',
                                         'css:.job-primary .name', 'css:h1'])
            company_name = self._ele_text(tab, ['css:.company-info .company-name',
                                                'css:.job-detail-company .company-name',
                                                'css:.company-name'])
            company_url = ''
            try:
                a = tab.ele('css:a[href*="/gongsi/"]', timeout=1)
                if a:
                    company_url = urljoin(url, a.attr('href') or '')
            except Exception:
                pass
            job = ja.parse_job(raw, title_hint=title, url=url)
            company = ja.parse_company(raw, name_hint=company_name, url=company_url)

            if company_url:
                self.msg_q.put(('analysis_status', '正在读取公司主页…'))
                extra = self._collect_company_page(page, company_url)
                if extra:
                    for key in ('name', 'industry', 'size', 'stage', 'address', 'intro',
                                'founded', 'reg_capital', 'insured', 'scope'):
                        if extra.get(key) and (key in ('intro', 'founded', 'reg_capital',
                                                       'insured', 'scope') or not company.get(key)):
                            company[key] = extra[key]
            return job, company
        finally:
            if created and tab is not None:
                try:
                    tab.close()
                except Exception:
                    pass

    def _collect_company_page(self, page, url):
        tab = None
        created = False
        try:
            tab = page.new_tab(url)
            created = True
            if self._wait_tab_ready(tab, 12) != 'ok':
                return None
            raw = self._detail_raw(tab)
            if len(raw) < 60:
                return None
            return ja.parse_company(raw, url=url)
        except Exception:
            return None
        finally:
            if created and tab is not None:
                try:
                    tab.close()
                except Exception:
                    pass

    def _collect_from_current(self):
        if self.page is None:
            raise RuntimeError('浏览器还没启动。先点「打开网页版」，登录后打开职位列表并点开一个职位。')
        page = self.page
        if 'zhipin.com' not in (page.url or ''):
            raise RuntimeError('当前浏览器页面不是 Boss 直聘，请先打开职位列表。')
        panel = None
        try:
            panel = page.ele('css:.job-detail-box', timeout=2)
        except Exception:
            pass
        if panel is None:
            raise RuntimeError('没找到职位详情面板：请先在职位列表里点开一个职位，再点这个按钮。')
        raw = (panel.text or '').strip()
        if len(raw) < 60:
            raise RuntimeError('详情面板内容太少，可能还没加载完，等 2 秒再试。')
        title = self._ele_text(panel, ['css:.job-banner .name h1', 'css:.job-banner .name',
                                       'css:h1'])
        company_name = self._ele_text(panel, ['css:.company-info .company-name',
                                              'css:.company-name'])
        job = ja.parse_job(raw, title_hint=title, url=page.url or '')
        company = ja.parse_company(raw, name_hint=company_name)
        return job, company

    def _collect_from_text(self, text):
        job = ja.parse_job(text)
        company = ja.parse_company(text)
        if not job.get('title'):
            raise RuntimeError('没能识别出岗位名称，请在粘贴内容第一行写上岗位名，例如「Java开发工程师」。')
        return job, company

    def _current_job_url(self, page):
        try:
            for loc in ('css:.job-card-wrap.active a.job-name',
                        'css:.job-card-box.active a.job-name',
                        'css:.job-card-wrapper.active a.job-name',
                        'css:a.job-name.active'):
                el = page.ele(loc, timeout=0.5)
                if el and el.attr('href'):
                    return urljoin('https://www.zhipin.com', el.attr('href'))
        except Exception:
            pass
        return page.url or ''

    # --------------------------------------------------------- 结果与报告
    def _set_analysis_busy(self, busy):
        self.analysis_busy = busy
        state = 'disabled' if busy else 'normal'
        for btn in self.analysis_buttons:
            btn.config(state=state)
        self.btn_ai.config(state=state)
        self.cmb_engine.config(state='disabled' if busy else 'readonly')

    def _insert_inline(self, text, tags=()):
        parts = text.split('**')
        for i, part in enumerate(parts):
            if not part:
                continue
            t = list(tags)
            if i % 2:
                t.append('bold')
            self.txt_result.insert('end', part, tuple(t))

    def _render_markdown(self, md):
        w = self.txt_result
        w.config(state='normal')
        w.delete('1.0', 'end')
        for raw in md.split('\n'):
            line = raw.rstrip()
            if not line.strip():
                w.insert('end', '\n')
                continue
            if line.startswith('#### '):
                w.insert('end', line[5:] + '\n', ('h3',))
            elif line.startswith('### '):
                w.insert('end', line[4:] + '\n', ('h3',))
            elif line.startswith('## '):
                w.insert('end', line[3:] + '\n', ('h2',))
            elif line.startswith('# '):
                w.insert('end', line[2:] + '\n', ('h1',))
            elif line.startswith('> '):
                tag = ('quote', 'warn') if '⚠️' in line else ('quote',)
                w.insert('end', '│ ' + line[2:] + '\n', tag)
            elif re.match(r'^\s*[-*]\s+', line):
                indent = '    ' if line.startswith(('  - ', '    - ')) else ''
                content = re.sub(r'^\s*[-*]\s+', '', line)
                w.insert('end', indent + '• ', ('bullet',))
                self._insert_inline(content, ('bullet',))
                w.insert('end', '\n', ('bullet',))
            elif line.strip() in ('---', '***'):
                w.insert('end', '─' * 46 + '\n', ('rule',))
            elif line.startswith('|'):
                w.insert('end', line + '\n', ('quote',))
            else:
                self._insert_inline(line)
                w.insert('end', '\n')
        w.config(state='disabled')

    def _on_analysis_done(self, md, result, path):
        self.last_report_md = md
        self.last_report_meta = result.get('meta') or {}
        self.last_report_path = path
        self._render_markdown(md)
        self._set_analysis_busy(False)
        self.btn_save_report.config(state='normal')
        self.btn_copy_report.config(state='normal')
        engine = 'AI' if result.get('engine') == 'llm' else '本地规则'
        note = f'分析完成（{engine}）｜报告已保存'
        if result.get('fallback_reason'):
            note = '分析完成（本地规则）｜AI 失败已降级'
        if not path:
            note += '｜保存失败，可点「另存一份」重试'
        self.lbl_analysis.config(text=note, foreground='#1a7f37')
        self._append_log(f'分析完成：{self.last_report_meta.get("company", "")} '
                         f'{self.last_report_meta.get("title", "")}'
                         + (f'｜{path}' if path else ''))
        try:
            self.cfg['manual_engine'] = self._engine_choice()
            ja.save_config(self.cfg)
        except Exception:
            pass

    def _on_analysis_error(self, msg):
        self._set_analysis_busy(False)
        self.lbl_analysis.config(text='分析失败（详见弹窗）', foreground='#b00020')
        messagebox.showerror('分析失败', msg + '\n\n可以改用「粘贴内容」：把 JD 和公司介绍直接粘进去。')

    def on_save_report(self):
        if not self.last_report_md:
            return
        try:
            meta = self.last_report_meta or {}
            path = ja.save_report(self.last_report_md, meta.get('company', ''),
                                  meta.get('title', ''))
            self.last_report_path = path
            self.lbl_analysis.config(text=f'已保存：{path}', foreground='#1a7f37')
        except Exception as e:
            self.report_error('保存报告失败', e)

    def on_open_reports(self):
        try:
            os.makedirs(ja.REPORT_DIR, exist_ok=True)
            os.startfile(ja.REPORT_DIR)
        except Exception as e:
            messagebox.showerror('打开失败', f'打不开报告目录：{e}\n路径：{ja.REPORT_DIR}')

    def on_copy_report(self):
        if not self.last_report_md:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.last_report_md)
            self.lbl_analysis.config(text='报告已复制到剪贴板', foreground='#1a7f37')
        except Exception as e:
            self.report_error('复制失败', e)

    # --------------------------------------------------- 发送前自动分析
    def _auto_analyze_current(self, page, index):
        if self.batch_report is None:
            return None
        try:
            panel = None
            try:
                panel = page.ele('css:.job-detail-box', timeout=1.5)
            except Exception:
                pass
            raw = (panel.text or '').strip() if panel else ''
            if len(raw) < 80:
                self.log('自动分析：详情面板内容太少，跳过')
                return None
            title = self._ele_text(panel, ['css:.job-banner .name h1', 'css:.job-banner .name',
                                           'css:h1']) if panel else ''
            company_name = self._ele_text(panel, ['css:.company-info .company-name',
                                                  'css:.company-name']) if panel else ''
            job = ja.parse_job(raw, title_hint=title, url=self._current_job_url(page))
            company = ja.parse_company(raw, name_hint=company_name)
            if not job.get('title'):
                self.log('自动分析：没识别到岗位名称，跳过')
                return None
            profile = {'resume': self.resume_for_run, 'expect': self.profile.get('expect', '')}
            result = ja.analyze(job, company, self.resume_for_run, engine=self.auto_engine,
                                cfg=self.cfg_snapshot, profile=profile)
            self.batch_report.add(result['markdown'], index)
            meta = result.get('meta') or {}
            if result.get('fallback_reason') and self.auto_engine == 'llm':
                self.auto_engine = 'local'
                self.log('AI 自动分析失败，后续职位改用本地规则，避免每个职位都等超时')
            self.log(f'自动分析 #{index}：{meta.get("company") or company.get("name") or "目标公司"}'
                     f'｜{job.get("title")}（匹配度 {meta.get("score", "-")}）')
            return result
        except Exception as e:
            self.log(f'自动分析失败（不影响发送）：{e}')
            return None

    # -------------------------------------------------------------- 消息队列
    def _poll(self):
        try:
            while True:
                kind, val = self.msg_q.get_nowait()
                if kind == 'log':
                    self._append_log(val)
                elif kind == 'pc':
                    self.state_pc = val
                    self.lbl_pc.config(text='已启动' if val else '未检测到',
                                       foreground='#1a7f37' if val else '#999999')
                elif kind == 'web':
                    self.state_web = val
                    self.lbl_web.config(text='已连接' if val else '未连接',
                                        foreground='#1a7f37' if val else '#999999')
                elif kind == 'progress':
                    self.lbl_progress.config(text=f'已发送：{val}')
                elif kind == 'signal':
                    self.root.bell()
                    self.root.deiconify()
                    self.root.lift()
                    self.root.focus_force()
                elif kind == 'running':
                    running = val
                    self.btn_start.config(state='disabled' if running else 'normal')
                    self.btn_open.config(state='disabled' if running else 'normal')
                    self.btn_stop.config(state='normal' if running else 'disabled')
                    self.btn_city.config(state='disabled' if running else 'normal')
                elif kind == 'analysis_status':
                    self.lbl_analysis.config(text=val, foreground='#0b5a3c')
                elif kind == 'analysis_done':
                    self._on_analysis_done(*val)
                elif kind == 'analysis_error':
                    self._on_analysis_error(val)
                elif kind == 'batch_done':
                    self.lbl_analysis.config(
                        text='批次分析完成，报告已存到 reports 文件夹', foreground='#1a7f37')
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _append_log(self, text):
        self.txt_log.config(state='normal')
        self.txt_log.insert('end', f'[{datetime.now().strftime("%H:%M:%S")}] {text}\n')
        self.txt_log.see('end')
        self.txt_log.config(state='disabled')

    def log(self, text):
        self.msg_q.put(('log', text))

    def report_error(self, prefix, e):
        """记录错误：日志区显示简短原因，完整堆栈写入 error.log"""
        try:
            err_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'error.log')
            with open(err_file, 'a', encoding='utf-8') as f:
                f.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {prefix}: {e}\n')
                f.write(traceback.format_exc() + '\n')
        except Exception:
            pass
        self.log(f'{prefix}：{e}（详情见 error.log）')

    def _sync_auto_open(self):
        self.auto_open = self.chk_auto_open_var.get()

    # ---------------------------------------------------------------- 监听
    def monitor_loop(self):
        """监听线程：检测 Boss直聘 PC 客户端进程 与 网页版标签页"""
        while not self.stop_all.is_set():
            try:
                pc = self._detect_pc_client()
                web = self._detect_web()
            except Exception:
                pc, web = self.state_pc, self.state_web

            if pc != self.state_pc:
                self.msg_q.put(('pc', pc))
                if pc:
                    self.log('检测到 Boss直聘 PC 客户端已启动')
            if web != self.state_web:
                self.msg_q.put(('web', web))
                self.log('Boss直聘网页版已连接' if web else 'Boss直聘网页版已断开')

            # 收到信号：自动启动网页版并激活窗口
            busy = self.send_thread is not None and self.send_thread.is_alive()
            if pc and not web and self.auto_open and not busy:
                self.msg_q.put(('signal', True))
                self.log('收到Boss直聘信号，自动打开网页版...')
                try:
                    self.ensure_browser()
                    with self.browser_lock:
                        self.page.get(HOME_URL)
                    self.log('网页版已打开，请在浏览器中完成登录（首次使用）')
                except Exception as e:
                    self.report_error('自动打开网页版失败', e)
                time.sleep(5)

            time.sleep(2)

    def _detect_pc_client(self):
        """检测 Boss直聘 PC 客户端进程"""
        try:
            for proc in psutil.process_iter(['name']):
                name = (proc.info.get('name') or '').lower()
                if 'boss' in name and 'python' not in name:
                    return True
        except Exception:
            pass
        return False

    def _detect_web(self):
        """检测由本工具管理的浏览器中是否存在 zhipin.com 标签页"""
        with self.browser_lock:
            page = self.page
        if page is None:
            return False
        try:
            for tid in page.tab_ids:
                tab = page.get_tab(tid)
                if tab and 'zhipin.com' in (tab.url or ''):
                    return True
        except Exception:
            pass
        return False

    # -------------------------------------------------------------- 浏览器
    def ensure_browser(self):
        """启动/接管浏览器（DrissionPage），返回页面对象"""
        with self.browser_lock:
            if self.page is not None:
                return self.page
        with self.browser_start_lock:
            with self.browser_lock:
                if self.page is not None:
                    return self.page
            try:
                from DrissionPage import ChromiumPage, ChromiumOptions
            except ImportError:
                raise RuntimeError('缺少 DrissionPage 库，请先执行：pip install DrissionPage')

            co = ChromiumOptions()
            path = find_browser_path()
            if path:
                co.set_browser_path(path)
            # 注意：该版本 set_user_data_path 会关闭 auto_port，二者不可同用；
            # 固定端口 + 固定配置目录，可保证登录状态持久化
            co.set_local_port(BROWSER_PORT)
            profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'boss_profile')
            co.set_user_data_path(profile)
            co.set_argument('--window-size=1280,880')
            page = ChromiumPage(co)
            with self.browser_lock:
                self.page = page
                self.browser_launched = True
            return page

    def on_open_browser(self):
        try:
            page = self.ensure_browser()
            page.get(HOME_URL)
            self.log('已打开 Boss直聘网页版，如未登录请扫码登录')
        except Exception as e:
            self.report_error('打开网页版失败', e)

    # ------------------------------------------------------------ 发送任务
    def on_start(self):
        if self.send_thread is not None and self.send_thread.is_alive():
            return
        if self.analysis_busy:
            messagebox.showinfo('请稍等', '正在分析职位，等分析结束再开始发送，避免抢浏览器。')
            return
        try:
            self.max_sends = max(1, int(self.spin_max.get()))
            self.min_delay = max(1, float(self.spin_dmin.get()))
            self.max_delay = max(self.min_delay, float(self.spin_dmax.get()))
        except ValueError:
            messagebox.showwarning('参数错误', '发送上限与间隔需为数字')
            return
        self.greeting_text = self.txt_greeting.get('1.0', 'end').strip()
        self.keyword = self.ent_keyword.get().strip()
        self.cities = list(self.selected_cities) or ['不限']
        self.industry = self.cmb_industry.get().strip() or '不限'

        # 分析相关：把这些值快照下来，后台线程不直接读 Tk 控件
        self.auto_analyze_flag = bool(self.var_auto_analyze.get())
        self.auto_engine = ('llm' if self.cmb_auto_engine.get().startswith('AI') else 'local')
        self.resume_for_run = self._resume_text()
        if self.resume_for_run:
            try:
                self.profile = ja.save_profile(self.resume_for_run, self.ent_expect.get().strip())
            except Exception:
                pass
        if self.auto_analyze_flag and not self.resume_for_run:
            if not messagebox.askyesno(
                    '还没填简历',
                    '没有简历时，发送前分析只能生成公司和岗位部分，无法给匹配度和技能差距。\n\n'
                    '继续只分析公司和岗位吗？选「否」会关闭本次自动分析。'):
                self.auto_analyze_flag = False
                self.var_auto_analyze.set(False)
                self.log('未填写简历，本次已关闭发送前自动分析')
        if self.auto_analyze_flag and self.auto_engine == 'llm' and not ja.has_api_key(self.cfg):
            self.log('未配置 AI Key，自动分析改用本地规则')
            self.auto_engine = 'local'
            self.cmb_auto_engine.set('本地规则')
        self.cfg['auto_analyze'] = self.auto_analyze_flag
        self.cfg['auto_engine'] = self.auto_engine
        try:
            ja.save_config(self.cfg)
        except Exception:
            pass
        self.cfg_snapshot = dict(self.cfg)

        self.batch_report = None
        if self.auto_analyze_flag:
            try:
                self.batch_report = ja.BatchReport('批次')
                engine_name = 'AI' if self.auto_engine == 'llm' else '本地规则'
                self.log(f'已开启发送前自动分析（{engine_name}），报告：{self.batch_report.path}')
            except Exception as e:
                self.log(f'创建批次报告失败，自动分析已关闭：{e}')
                self.batch_report = None

        self.target_urls = self._build_target_urls()
        if len(self.target_urls) > 1:
            self.log(f'已选 {len(self.target_urls)} 个城市，将按顺序逐个搜索；'
                     f'发送上限 {self.max_sends} 条在所有城市间共用')
        self.processed = set()
        self.stop_run.clear()
        self.msg_q.put(('progress', 0))
        self.msg_q.put(('running', True))
        self.log('任务启动')
        self.send_thread = threading.Thread(target=self.send_loop, daemon=True)
        self.send_thread.start()

    def on_stop(self):
        self.stop_run.set()
        self.log('已请求停止，等待当前步骤结束...')

    def _build_target_urls(self):
        """把多选城市拆成多个列表页 URL（Boss 的搜索链接一次只认一个城市）。"""
        names = [c for c in (self.cities or ['不限']) if c]
        if not names or '不限' in names:
            return [{'label': '不限', 'url': self._jobs_url('')}]
        if '全国' in names:
            return [{'label': '全国', 'url': self._jobs_url(CITY_MAP['全国'])}]

        urls, seen = [], set()
        for name in names:
            code = str(CITY_MAP.get(name, name))
            if code in seen:
                continue
            seen.add(code)
            urls.append({'label': name, 'url': self._jobs_url(code)})
        return urls or [{'label': '不限', 'url': self._jobs_url('')}]

    def _jobs_url(self, city_code):
        params = []
        if self.keyword:
            params.append('query=' + quote(self.keyword))
        if city_code:
            params.append('city=' + str(city_code))
        return JOBS_URL + ('?' + '&'.join(params) if params else '')

    def send_loop(self):
        sent = 0
        aborted = False
        try:
            page = self.ensure_browser()
            total = len(self.target_urls)
            for idx, city_target in enumerate(self.target_urls, 1):
                if self.stop_run.is_set() or sent >= self.max_sends:
                    break
                self.current_url = city_target['url']
                progress = f'（{idx}/{total}）' if total > 1 else ''
                self.log(f'打开职位列表{progress}：{city_target["label"]} {city_target["url"]}')
                page.get(city_target['url'])
                if not self._wait_page_ready(page):
                    aborted = True
                    break
                if self.industry not in ('', '不限'):
                    self._apply_industry_filter(page, self.industry)

                remaining_sends = self.max_sends - sent
                remaining_cities = total - idx + 1
                city_quota = min(remaining_sends,
                                 max(1, (remaining_sends + remaining_cities - 1) // remaining_cities))
                city_sent = 0
                if total > 1:
                    self.log(f'{city_target["label"]}：分配 {city_quota} 条'
                             f'（剩余 {remaining_sends} 条 / {remaining_cities} 个城市）')
                self.log(f'开始扫描 {city_target["label"]} 的职位列表...')

                missed_rounds = 0
                while (not self.stop_run.is_set() and sent < self.max_sends
                       and city_sent < city_quota):
                    if 'security-check' in (page.url or ''):
                        if not self._wait_security_check(page):
                            self.log('安全验证未完成，任务已停止。')
                            aborted = True
                            break

                    cards = self._find_cards(page)
                    target_card = None
                    for a in cards:
                        key = a.attr('href') or (a.text or '')
                        if key and key not in self.processed:
                            target_card = (a, key)
                            break

                    if target_card is None:
                        # 滚动加载更多职位
                        self._scroll_list(page)
                        time.sleep(2.2)
                        if not self._has_new_cards(page):
                            missed_rounds += 1
                            if missed_rounds >= 3:
                                tail = '，切换下一个城市' if idx < total else '，发送结束'
                                self.log(f'{city_target["label"]} 没有更多可打招呼的职位{tail}')
                                break
                        continue
                    missed_rounds = 0

                    if self._greet_card(page, target_card[0], target_card[1], sent + 1):
                        sent += 1
                        city_sent += 1
                        self.msg_q.put(('progress', sent))
                        self.log(f'已发送 {sent}/{self.max_sends}')
                        delay = random.uniform(self.min_delay, self.max_delay)
                        self.log(f'随机等待 {delay:.1f} 秒...')
                        self._sleep_stop(delay)

                if aborted:
                    break
                if (not self.stop_run.is_set() and sent < self.max_sends
                        and city_sent >= city_quota and idx < total):
                    self.log(f'{city_target["label"]} 已达到分配额度，切换下一个城市')
                if total > 1 and not self.stop_run.is_set():
                    self.log(f'{city_target["label"]} 处理结束，累计发送 {sent} 条')

            if aborted:
                self.log(f'任务提前结束，已发送 {sent} 条问候语。')
            else:
                self.log(f'本次任务结束，共发送 {sent} 条问候语。')
        except Exception as e:
            self.report_error('任务异常终止', e)
        finally:
            if self.batch_report is not None:
                try:
                    if self.batch_report.count:
                        path = self.batch_report.finish()
                        self.log(f'批次分析报告已保存（{self.batch_report.count} 个职位）：{path}')
                        self.msg_q.put(('batch_done', path))
                    else:
                        try:
                            os.remove(self.batch_report.path)
                        except OSError:
                            pass
                        self.log('本次没有职位完成自动分析，未生成批次报告')
                except Exception as e:
                    self.log(f'批次报告保存失败：{e}')
                self.batch_report = None
            self.msg_q.put(('running', False))

    def _sleep_stop(self, seconds):
        """可被停止打断的等待"""
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_run.is_set():
                return
            time.sleep(0.3)

    def _wait_page_ready(self, page, timeout=240):
        """等待页面就绪（已登录且列表可见），期间输出进度日志"""
        self.log('等待页面就绪，如未登录请在工具浏览器窗口中扫码登录...')
        start = time.time()
        deadline = start + timeout
        last_log = 0.0
        while time.time() < deadline and not self.stop_run.is_set():
            try:
                url = page.url or ''
                if 'zhipin.com' in url and 'security-check' not in url:
                    # 已登录标识：右上角头像
                    if page.eles('css:.nav-figure', timeout=1) and self._find_cards(page):
                        self.log(f'页面就绪（等待了 {int(time.time() - start)} 秒）')
                        return True
            except Exception:
                pass
            now = time.time()
            if now - last_log >= 15:
                last_log = now
                self.log(f'仍在等待（已等待 {int(now - start)} 秒），请确认已在工具浏览器窗口中登录...')
            time.sleep(2)
        if self.stop_run.is_set():
            self.log('任务已停止。')
        else:
            self.log('等待超时：未检测到登录状态或职位列表，任务停止。')
        return False

    def _wait_security_check(self, page, timeout=180):
        """若触发风控页面，等待用户手动完成验证"""
        deadline = time.time() + timeout
        warned = False
        while time.time() < deadline and not self.stop_run.is_set():
            try:
                if 'security-check' not in (page.url or ''):
                    return True
                if not warned:
                    self.log('检测到平台安全验证，请在浏览器窗口手动完成验证...')
                    warned = True
            except Exception:
                return True
            time.sleep(2)
        return False

    # --------------------------------------------------------- 页面元素
    def _find_cards(self, page):
        """查找列表中的职位卡片（返回 a.job-name 元素，href 为唯一键）"""
        for loc in ('css:.job-card-wrap a.job-name', 'css:.job-card-wrapper a.job-name',
                    'css:a.job-name'):
            try:
                els = page.eles(loc, timeout=2)
            except Exception:
                els = []
            els = [e for e in els if e.attr('href')]
            if els:
                return els
        return []

    def _has_new_cards(self, page):
        """滚动后检查是否还有未处理的新职位"""
        for a in self._find_cards(page):
            key = a.attr('href') or (a.text or '')
            if key and key not in self.processed:
                return True
        return False

    def _scroll_list(self, page):
        """滚动职位列表（优先内层滚动容器）"""
        try:
            lst = page.ele('css:.rec-job-list', timeout=1) or \
                  page.ele('css:.job-list-container', timeout=1)
            if lst:
                lst.scroll.to_bottom()
                return
        except Exception:
            pass
        try:
            page.scroll.to_bottom()
        except Exception:
            pass

    def _click_card(self, page, a):
        """点击职位卡片（优先点击卡片容器，避免直接跳转链接）"""
        target = a
        try:
            li = a.parent('@class=job-card-box')
            if li:
                target = li
        except Exception:
            pass
        try:
            target.click()
        except Exception:
            a.click()

    def _apply_industry_filter(self, page, industry):
        """通过页面筛选控件应用"公司行业"过滤"""
        try:
            dd = page.ele('css:.condition-industry-select', timeout=3)
            if dd is None:
                self.log('未找到行业筛选控件，跳过行业筛选')
                return
            cur = dd.ele('css:.current-select', timeout=1) or dd
            cur.click()
            time.sleep(0.8)
            opt = None
            for a in dd.eles('tag:a', timeout=2):
                if (a.text or '').strip() == industry:
                    opt = a
                    break
            if opt is None:
                self.log(f'行业列表中未找到"{industry}"，跳过筛选')
                return
            opt.click()
            time.sleep(2.0)
            self.log(f'已应用行业筛选：{industry}')
        except Exception as e:
            self.log(f'应用行业筛选失败：{e}')

    def _ensure_filter(self, page):
        """返回列表页后，若行业筛选丢失则重新应用"""
        ind = getattr(self, 'industry', '') or ''
        if ind in ('', '不限'):
            return
        try:
            cur = page.ele('css:.condition-industry-select .current-select', timeout=2)
            if cur is not None and ind not in (cur.text or ''):
                self._apply_industry_filter(page, ind)
        except Exception:
            pass

    # --------------------------------------------------------- 沟通流程
    def _visible(self, el):
        """判断元素是否可见（NoneElement 视为不可见）"""
        if not el:
            return False
        try:
            return bool(el.state.is_displayed())
        except Exception:
            return True

    def _greet_card(self, page, a, key, index=0):
        """点击卡片 → 点击"立即沟通"（平台自动发送打招呼语）→ 可选补发介绍"""
        self.processed.add(key)
        self._click_card(page, a)

        # 在详情面板操作区定位沟通按钮
        btn = None
        for _ in range(6):
            if self.stop_run.is_set():
                return False
            try:
                detail = page.ele('css:.job-detail-box', timeout=1) or page
                for b in detail.eles('css:.job-detail-op a.op-btn', timeout=1):
                    t = (b.text or '').strip()
                    if t == '继续沟通':
                        self.log('该职位此前已沟通过，跳过')
                        return False
                    if t == '立即沟通':
                        btn = b
                        break
                if btn:
                    break
            except Exception:
                pass
            time.sleep(0.8)
        if btn is None:
            self.log('未找到沟通按钮，跳过该职位')
            return False

        # 发送前分析：此时详情面板已经加载，读文本即可，不额外开标签页
        if self.batch_report is not None and index:
            self._auto_analyze_current(page, index)
            if self.stop_run.is_set():
                return False

        try:
            btn.click()
        except Exception as e:
            self.log(f'点击沟通按钮失败：{e}')
            return False

        # 等待平台发送结果弹窗（"已向BOSS发送消息" = 成功）
        dlg = None
        end = time.time() + 12
        while time.time() < end and not self.stop_run.is_set():
            try:
                d = page.ele('css:.greet-boss-dialog', timeout=1)
                if self._visible(d):
                    dlg = d
                    break
            except Exception:
                pass
            time.sleep(0.6)

        if dlg is None:
            self.log('未出现发送确认弹窗，该职位可能未发送成功，跳过')
            return False

        # 读取弹窗内容：确认成功 / 检测当日上限
        try:
            body = dlg.text or ''
        except Exception:
            body = ''
        if ('上限' in body) or ('机会已用' in body) or ('明天再来' in body):
            self.log('今日打招呼机会已达平台上限，任务停止。')
            self.stop_run.set()
            return False
        if '已向BOSS发送消息' not in body:
            self.log(f'平台返回未知弹窗：{body[:40]}，跳过该职位')
            try:
                c = dlg.ele('css:.cancel-btn', timeout=1)
                if c:
                    c.click()
            except Exception:
                pass
            return False

        greet_con = ''
        try:
            con = dlg.ele('css:.greet-con', timeout=1)
            if con:
                greet_con = (con.text or '').strip()
        except Exception:
            pass
        self.log(f'打招呼语已发送：{greet_con[:36]}')

        if self.greeting_text:
            self._send_followup(page, dlg)
        else:
            try:
                cancel = dlg.ele('css:.cancel-btn', timeout=1)
                if cancel:
                    cancel.click()
                time.sleep(0.6)
            except Exception:
                pass
        return True

    def _send_followup(self, page, dlg):
        """点击"继续沟通"打开会话，补发一条自定义介绍（尽力而为）"""
        opened = False
        try:
            sure = dlg.ele('css:.sure-btn', timeout=2)
            if sure:
                sure.click()
                opened = True
            time.sleep(2.5)
        except Exception:
            pass

        box = self._find_chat_box(page) if opened else None
        sent = False
        if box is not None:
            try:
                box.input(self.greeting_text)
                time.sleep(0.5)
            except Exception as e:
                self.log(f'输入补充介绍失败：{e}')
            for loc in ('css:.btn-send', 'text=发送'):
                try:
                    b = page.ele(loc, timeout=1.5)
                    if b:
                        b.click()
                        sent = True
                        break
                except Exception:
                    continue

        if sent:
            self.log('已补发自定义介绍消息')
        else:
            self.log('未能补发自定义介绍（打招呼语已发送）。个性化内容建议在Boss直聘【消息通知-设置招呼语】中配置')
        self._reload_list(page)

    def _find_chat_box(self, page):
        """在页面及聊天 iframe 中查找聊天输入框"""
        scopes = [page]
        try:
            fr = page.get_frame('css:iframe[src*="chat"]')
            if fr:
                scopes.insert(0, fr)
        except Exception:
            pass
        for scope in scopes:
            for loc in ('css:#chat-input', 'css:div[contenteditable="true"]',
                        'css:textarea', 'css:.edit-area', 'css:.chat-input'):
                try:
                    b = scope.ele(loc, timeout=0.8)
                    if b:
                        return b
                except Exception:
                    continue
        return None

    def _reload_list(self, page):
        """整页刷新职位列表并恢复筛选"""
        try:
            page.get(self.current_url)
            time.sleep(1.5)
            self._ensure_filter(page)
        except Exception:
            pass

    # ---------------------------------------------------------------- 退出
    def _on_close(self):
        self.stop_all.set()
        self.stop_run.set()
        try:
            with self.browser_lock:
                page, launched = self.page, self.browser_launched
                self.page = None
            if page is not None and launched:
                page.quit()
        except Exception:
            pass
        self.root.destroy()


class CityPickerDialog(tk.Toplevel):
    """多选城市：勾选后按顺序逐个城市搜索，共用发送上限。"""

    def __init__(self, parent, selected, on_ok):
        super().__init__(parent)
        self.title('选择城市（可多选）')
        self.resizable(False, False)
        self.transient(parent)
        self.on_ok = on_ok
        self.vars = {}
        selected_list = list(selected or ['不限'])
        extra_codes = [n for n in selected_list if n not in CITY_MAP]
        selected = set(selected_list)

        wrap = ttk.Frame(self)
        wrap.pack(fill='both', expand=True, padx=12, pady=(10, 4))
        ttk.Label(wrap, foreground='#666666', justify='left',
                  text='可多选：会按顺序逐个城市搜索并发送，共用发送上限。'
                       '「不限」「全国」与其他城市互斥。').pack(anchor='w', pady=(0, 6))

        grid = ttk.Frame(wrap)
        grid.pack(fill='both', expand=True)
        cols = 4
        for i, name in enumerate(CITY_MAP.keys()):
            var = tk.BooleanVar(value=name in selected)
            self.vars[name] = var
            ttk.Checkbutton(grid, text=name, variable=var,
                            command=lambda n=name: self._on_check(n)
                            ).grid(row=i // cols, column=i % cols,
                                   sticky='w', padx=6, pady=2)

        row_custom = ttk.Frame(wrap)
        row_custom.pack(fill='x', pady=(8, 0))
        ttk.Label(row_custom, text='其他城市代码：').pack(side='left')
        self.ent_custom = ttk.Entry(row_custom, width=24)
        self.ent_custom.pack(side='left', padx=(0, 6))
        if extra_codes:
            self.ent_custom.insert(0, ','.join(extra_codes))
        ttk.Label(row_custom, text='Boss 城市代码，逗号分隔（可留空）',
                  foreground='#777777').pack(side='left')

        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=12, pady=10)
        ttk.Button(bar, text='全选', width=7, command=self._select_all).pack(side='left')
        ttk.Button(bar, text='清空', width=7, command=self._clear_all).pack(side='left', padx=6)
        ttk.Button(bar, text='取消', width=8, command=self.destroy).pack(side='right')
        ttk.Button(bar, text='确定', width=8, command=self._ok).pack(side='right', padx=(0, 6))

        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda e: self.destroy())
        self.grab_set()
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = min(max(0, px + 120), max(0, sw - w - 20))
        y = min(max(0, py + 120), max(0, sh - h - 50))
        self.geometry(f'+{x}+{y}')
        self.focus_force()

    def _on_check(self, name):
        if not self.vars[name].get():
            return
        if name in ('不限', '全国'):
            for other, var in self.vars.items():
                if other != name:
                    var.set(False)
        else:
            for special in ('不限', '全国'):
                self.vars[special].set(False)

    def _select_all(self):
        for name, var in self.vars.items():
            var.set(name not in ('不限', '全国'))

    def _clear_all(self):
        for var in self.vars.values():
            var.set(False)

    def _ok(self):
        names = [name for name, var in self.vars.items() if var.get()]
        raw = re.split(r'[,，;；\s]+', self.ent_custom.get().strip())
        codes = [x for x in raw if x]
        code_to_name = {str(v): k for k, v in CITY_MAP.items() if v}
        for code in codes:
            if not code.isdigit():
                messagebox.showwarning(
                    '城市代码不对',
                    f'「{code}」不是数字。\nBoss 城市代码可以看城市选择页 URL 里的 city= 后面那串，'
                    '例如北京是 101010100。', parent=self)
                return
            name = code_to_name.get(code, code)
            if name not in names:
                names.append(name)

        specifics = [n for n in names if n not in ('不限', '全国')]
        if specifics:
            names = specifics
        if not names:
            names = ['不限']
        self.on_ok(names)
        self.destroy()


class AISettingsDialog(tk.Toplevel):
    """AI 接口配置：Base URL / 模型 / API Key，支持测试连接。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title('AI 设置（OpenAI 兼容接口）')
        self.geometry('640x380')
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        self._test_q = queue.Queue()

        cfg = app.cfg or ja.load_config()
        self.var_base = tk.StringVar(value=cfg.get('llm_base_url') or ja.DEFAULT_CONFIG['llm_base_url'])
        self.var_model = tk.StringVar(value=cfg.get('llm_model') or ja.DEFAULT_CONFIG['llm_model'])
        self.var_key = tk.StringVar(value=cfg.get('llm_api_key') or '')
        self.var_show = tk.BooleanVar(value=False)

        pad = dict(padx=12, pady=6)
        ttk.Label(self, foreground='#666666', wraplength=600, justify='left',
                  text='默认接 DeepSeek：Base URL 填 https://api.deepseek.com/v1，模型 deepseek-chat。'
                       '任何 OpenAI 兼容接口（通义、Kimi、智谱、本地 Ollama 等）都可以用。'
                       'Key 只保存在本机 config.json（明文），不要用别人的 Key。').pack(fill='x', **pad)

        form = ttk.Frame(self)
        form.pack(fill='x', padx=12)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text='Base URL：').grid(row=0, column=0, sticky='w', pady=4)
        ttk.Entry(form, textvariable=self.var_base).grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Label(form, text='模型名：').grid(row=1, column=0, sticky='w', pady=4)
        ttk.Entry(form, textvariable=self.var_model).grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(form, text='API Key：').grid(row=2, column=0, sticky='w', pady=4)
        self.ent_key = ttk.Entry(form, textvariable=self.var_key, show='*')
        self.ent_key.grid(row=2, column=1, sticky='ew', pady=4)
        ttk.Checkbutton(form, text='显示', variable=self.var_show,
                        command=self._toggle_key).grid(row=2, column=2, padx=(8, 0))

        self.lbl_test = ttk.Label(self, text='填好后建议先点「测试连接」。', foreground='#777777',
                                  wraplength=600, justify='left')
        self.lbl_test.pack(fill='x', padx=12, pady=(8, 0))

        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=12, pady=12)
        self.btn_test = ttk.Button(bar, text='测试连接', command=self._test, width=12)
        self.btn_test.pack(side='left')
        ttk.Button(bar, text='取消', command=self.destroy, width=10).pack(side='right')
        ttk.Button(bar, text='保存', command=self._save, width=10).pack(side='right', padx=(0, 8))

        self.protocol('WM_DELETE_WINDOW', self.destroy)

    def _toggle_key(self):
        self.ent_key.config(show='' if self.var_show.get() else '*')

    def _current_cfg(self):
        return {
            'llm_base_url': self.var_base.get().strip() or ja.DEFAULT_CONFIG['llm_base_url'],
            'llm_model': self.var_model.get().strip() or ja.DEFAULT_CONFIG['llm_model'],
            'llm_api_key': self.var_key.get().strip(),
            'llm_timeout': self.app.cfg.get('llm_timeout', 120),
            'llm_max_tokens': self.app.cfg.get('llm_max_tokens', 2200),
        }

    def _save(self):
        cfg = dict(self.app.cfg or ja.load_config())
        cfg.update(self._current_cfg())
        try:
            self.app.cfg = ja.save_config(cfg)
        except Exception as e:
            messagebox.showerror('保存失败', str(e), parent=self)
            return
        self.app._refresh_ai_state()
        self.app.log('AI 设置已保存')
        self.destroy()

    def _test(self):
        cfg = self._current_cfg()
        if not cfg['llm_api_key']:
            messagebox.showinfo('提示', '先填 API Key', parent=self)
            return
        self.btn_test.config(state='disabled')
        self.lbl_test.config(text='测试中，最多等 30 秒…', foreground='#0b5a3c')
        threading.Thread(target=self._test_worker, args=(cfg,), daemon=True).start()
        self.after(200, self._poll_test)

    def _test_worker(self, cfg):
        try:
            self._test_q.put(('ok', ja.llm_ping(cfg)))
        except Exception as e:
            self._test_q.put(('err', str(e)))

    def _poll_test(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            kind, msg = self._test_q.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_test)
            return
        self.btn_test.config(state='normal')
        if kind == 'ok':
            self.lbl_test.config(text=f'连接成功，模型回复：{msg}', foreground='#1a7f37')
        else:
            self.lbl_test.config(text='连接失败：' + msg[:90], foreground='#b00020')


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use('vista')
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
