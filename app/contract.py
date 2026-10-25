# -*- coding: utf-8 -*-
"""契约层：把她此刻的处境写成一段话，并对她的输出跑硬判据。

四件事：

**A1 强制引用自己上一句。** 不是靠提示词自觉，是代码验收：模型在【态】里必须写
`引用=上一句里的词`（或 追问/改口/自嘲/补完 这类动作）。收回来后逐字比对，
对不上就重写一次；还不行就这一格不说——宁可沉默，也不要又冒出一句接不上的话。

**A2 pending 分档清算。** 队列在调度层，结算规则见 router.pending_tick()。这里只
负责把队列写成一行话给她看，并且**压住讨债口吻**（问过的就不许再问）。

**A3 句子落点自检。** 把她这句话里「关于世界的词」删掉，剩下的必须还能看出她在
对他说话；剩不下东西 = 违约，重写。

**A4 落点在他。** 只查最后一句：这一轮的最后一句必须有「你/咱/我们」或他的名字。
画面只是由头，说完要回到你们俩。

还有一条**认亲边界**：只允许相邻一帧的近邻比较算同一个，隔一轮以上一律不算。
欲望分支依赖跨帧比较，不定义这条它就没有合法地基——所以这条写在 situation 里，
也写在 router 的 detect() 里（只比 tick-1）。
"""

import re

from textutil import bigrams as _bigrams, norm as _norm

# ---------- A3 用的「关于世界的词」 ----------

WORLD_WORDS = (
    "街道路口巷子台阶楼梯石阶阶梯坡道广场公园车站站台月台"
    "店铺店面门口门框柜台货架冰柜橱窗招牌灯牌霓虹广告海报"
    "楼房屋顶墙面砖缝玻璃窗窗帘栏杆扶手电线电线杆路灯红绿灯"
    "树花樱花瓣落叶草地泥土石头砖水坑积水喷泉池子湖海河岸边沙滩"
    "天空云太阳月亮影子光斑人人群路人店员客人队伍车汽车车门车厢"
    "自行车摩托车机器箱垃圾桶长椅椅子座位桌台布帘棚子亭子柱子"
)
WORLD_CHARS = WORLD_WORDS

# 这些词说明她在说自己身上的事，不是描述世界。
# 注意是**词**不是字：按字匹配，「招牌上写着冰淇淋」里的「上」会被当成身体词，
# A3 就形同虚设（这个坑踩过一次）。
BODY_WORDS = (
    "疼", "酸", "沉", "累", "麻", "软", "晃", "晕", "饿", "渴", "冷", "热", "晒",
    "怕", "烦", "喜欢", "讨厌", "不想说", "说不上", "手心", "出汗", "手指", "手指头",
    "脚", "腿", "膝盖", "肩膀", "喉咙", "眼眶", "后背", "头发", "挤", "抖", "喘",
    "跟不上", "松手", "牵着", "扣",
    # 下面这些是"动作/状态"，不带"我"也说明她在讲自己的处境
    "走", "站", "坐", "停", "等", "看", "听", "笑", "哭", "叹气", "回头",
    "低头", "抬头", "皱眉", "咬牙", "咽", "嘟囔", "争", "认输", "嘴硬",
)
SECOND = ("你", "咱", "我们", "跟我", "陪你")
# 没有"收回"：撤回自己那句话听着像在改稿，不像人说话。
# 要接不上就改口（"不是，我是说…"），不要"我刚才那句收回"。
ACTION_REFS = ("追问", "改口", "自嘲", "补完", "认输", "顶回去", "绕过")


def _strip_world(text):
    s = str(text or "")
    for w in sorted(set(WORLD_CHARS), key=len, reverse=True):
        s = s.replace(w, "")
    s = re.sub(r"[，。！？、；：…\s“”\"'「」（）()]", "", s)
    return s


def landing_test(line, call=""):
    """A3：删掉关于世界的词，看还剩不剩「她在对他说话」。

    这里踩过两个坑，都修在这版里：
    1. 只认「你」不认称呼 —— 「招财宝，左边那行红字我认得」被判违约，
       可她明明在叫他名字。所以 call（他对她的称呼）也算"对他说话"。
    2. 只认身体词不认「我」 —— 「我不想走了」被判违约，因为"走"当时不在词表里。
       现在只要剩下「我」，就说明这句是她的立场，不是导游解说。
    「灯亮了」「左边那行红字真亮」仍然违约——剩下的话里既没有我也没有你。
    """
    rest = _strip_world(line)
    has_second = any(w in rest for w in SECOND)
    has_self = "我" in rest
    has_body = any(w in rest for w in BODY_WORDS)
    has_call = bool(call) and str(call) in str(line or "")
    ok = bool(rest) and (has_self or has_second or has_body or has_call)
    return {
        "ok": ok,
        "rest": rest,
        "why": "" if ok else ("删掉景物的词之后只剩「%s」，看不出在对他说话" % (rest or "空")),
    }


_EMPTY_CHARS = "了的着吧吗呢啊就都也还很太是有在和跟他她它这个那吗嘛呀"

_STOP_BIGRAMS = set("""
我们 你们 他们 这个 那个 这样 那样 什么 怎么 不是 就是 还是 可以 一个 没有 已经
自己 现在 时候 有点 一点 觉得 知道 因为 所以 但是 然后 如果 真的 其实 好像 一样
""".split())


SECOND_PERSON = ("你", "咱", "我们", "咱们")

# 他是哑的，也不会给反应。出现这些说法就是把他当成会回话的人。
# 只收"向他要回应"的固定说法；不写光秃秃的"反应"，
# 否则她自嘲"我反应慢"也会被误判。
REACT_ASKS = ("给点反应", "给个反应", "有点反应", "反应一下",
              "应一声", "吱一声", "说句话", "回答我",
              "你倒是说话", "你倒是先开口")

# 故事格不要往童年跑：报年龄、说"小时候"很容易和别处对不上。
# 只收明确的童年说法，"岁数""年岁"这类不算。
CHILDHOOD_WORDS = ("小时候", "童年", "儿时", "岁那年", "那年我", "上学的时候", "小学")


def childhood_mention(line):
    for w in CHILDHOOD_WORDS:
        if w in (line or ""):
            return w
    return ""


# "读到一块牌子"不算心动——那是想要，不是心里软一下。
READING_WORDS = ("招牌", "牌子", "写着", "几个字", "念出来", "念不全")

# "夸一句"才算心动。只是描述景物、只是身体放松，不给好感。
# 会显示在字幕上的情绪：只有正面这三种。负面的不显示，让观众自己看。
MOOD_SHOWN = ("开心", "害羞", "感动")

PRAISE_WORDS = ("好看", "漂亮", "真美", "值了", "没白来", "不白来",
                "会挑地方", "挑得好", "真会挑", "舍不得走", "想多待",
                "多站一会儿", "站一会儿", "真好")


def asks_reaction(line):
    for w in REACT_ASKS:
        if w in (line or ""):
            return w
    return ""


def guard_checks(line):
    """两条提示词管不住、必须由代码兜底的：

    1. 童年/年龄——一说具体岁数就和别处设定对不上，一听就假；
    2. 向他要反应——他是哑的，问「你怎么不说话」「给点反应」就是把他当会回话的人。

    它们不是"判分"，只负责把这一句打回去重说一次。
    """
    child = childhood_mention(line)
    react = asks_reaction(line)
    problems = []
    if child:
        problems.append("提到了童年/年龄（%s）" % child)
    if react:
        problems.append("向他要反应（%s）" % react)
    return {"ok": not problems, "problems": problems,
            "childhood": child, "reaction": react}


# 读字格（branch=="sign"）的软判据：要么真读了，要么明说读不出。
# 不要求引号里一定有字——模型可能直接写「招牌上写的是 Felicita」。
READ_MARKS = (
    "写着", "写的是", "写的字", "上面写", "几个字", "字是",
    "念出", "念不", "认得字", "认得这字", "认得出来", "认不出", "认不全",
    "看不清", "没看清", "读不出", "读不全", "看不懂", "糊了", "模糊",
    "猜不出", "不认识", "字太小",
)
_QUOTE_MARK = re.compile(r"[「『“\"][^」』”\"]{1,24}[」』”\"]")


def reading_check(line, branch=""):
    """读字分支的软判据。

    这一格如果由「有一块字清楚到能读」触发，台词至少要落到字上：
    要么把认出来的字说出来（引号 / 写着 / 认得出…），
    要么明确说读不清。只讲旁边别的东西，就是没接这一格的事件——
    打回重说一次；两次都不过仍然收下，记 `reading_soft`，不整格丢掉。
    """
    if str(branch or "") != "sign":
        return {"ok": True, "how": "不适用"}
    s = str(line or "")
    if _QUOTE_MARK.search(s):
        return {"ok": True, "how": "引了原文"}
    for w in READ_MARKS:
        if w in s:
            return {"ok": True, "how": "读字:%s" % w}
    return {"ok": False, "how": "没读字",
            "why": "这一格是读字格：要么把认出来的字说出来，要么直说看不清/读不出，"
                   "别只讲旁边的东西；最后一句还要落在他身上。"}


# 看不清的小东西不要叫出名字。只查眼前，回忆/故事豁免。
UNSURE_ANIMAL_WORDS = (
    "小狗", "小猫", "小鸽子", "鸽子", "麻雀", "小鸟", "虫子", "老鼠",
    "兔子", "鸭子", "鹅", "狗", "猫", "鸟",
)

# 模型爱用的万用身体句：本身不算错，但出现太多次就像模板。
STOCK_PHRASES = (
    "眼睛还没跟上", "眼睛没跟上", "还没缓过来", "缓过来了", "眼睛不花了",
    "手心黏", "手还黏", "手心湿", "手心还湿", "手心有点黏", "手心有点湿",
)


# 他牵着她一直在走：她不该有"停下/站住"的概念，除非代码给了 pause。
STOP_WORDS = (
    "站住", "站着", "站定", "站这儿", "站在这儿", "站在这", "站一会", "站一下",
    "站站", "站在原地", "站了一",
    "停下", "停一下", "停一停", "停住", "停步", "先停", "停在原地",
    "挪不动", "走不动", "走不了", "迈不动", "迈不开", "拖不动", "抬不起腿",
    "不想走", "懒得走",
    "钉在原地", "定在原地",
    "先别走", "别走了", "不走了",
)


def _outside_quotes(s):
    """去掉引号里的内容：招牌/气泡原文里出现"进去/小狗/眼睛还没跟上"不算她说的。"""
    return _QUOTE_MARK.sub("", str(s or ""))


def small_thing_check(line, story=False):
    """眼前的小东西看不清，就别叫名字（小狗/鸽子/猫…）。

    这不是"事实判断"（代码也不知道那是不是狗），是写作纪律：
    画面里一团小的、认不准，就写「小东西」「那团小的」和它的动静。
    回忆里的狗是另一回事，带回忆标记或故事格一律放过。
    """
    if story:
        return {"ok": True, "how": "故事豁免"}
    raw = str(line or "")
    if any(k in raw for k in RECALL_MARKS) or any(k in raw for k in ("养过", "见过", "遇到过", "有过")):
        return {"ok": True, "how": "回忆豁免"}
    s = _outside_quotes(raw)
    # 招牌/海报/墙上画的动物不是"眼前的小东西"，别误伤。
    if any(k in s for k in ("画着", "画的", "画了", "图案", "招牌", "海报", "牌子上",
                            "涂鸦", "雕塑", "玩偶", "标志", "logo", "LOGO")):
        return {"ok": True, "how": "画/招牌上的"}
    hits = [w for w in UNSURE_ANIMAL_WORDS if w in s]
    hits = [w for w in hits if not any(w != o and w in o for o in hits)]
    if not hits:
        return {"ok": True, "how": "没叫名字"}
    return {"ok": False, "how": "叫了具体动物:%s" % "/".join(hits[:2]),
            "why": "看不清的小东西别叫名字（%s）：说「有个小东西」「那团小的」，"
                   "只写它的动静和大小。" % "、".join(hits[:2])}


def stock_check(line, story=False):
    """挡几句一出现就重复的万用身体句。

    「我眼睛还没跟上 / 缓过来了 / 眼睛不花了」原来是 transit 信号卡里的措辞，
    模型会直接搬进台词；灯串、风铃、招牌每格都用一遍，听起来像同一个人在念稿。
    换成具体反应：眯眼、停半拍、手挡光、脚下乱一下。
    """
    if story:
        return {"ok": True, "how": "故事豁免"}
    raw = str(line or "")
    if any(k in raw for k in RECALL_MARKS):
        return {"ok": True, "how": "回忆豁免"}
    s = _outside_quotes(raw)
    hits = [w for w in STOCK_PHRASES if w in s]
    if not hits:
        return {"ok": True, "how": "没套话"}
    return {"ok": False, "how": "套话:%s" % "/".join(hits[:2]),
            "why": "「%s」是套话：换一个具体的身体反应（眯一下眼、脚下停半拍、"
                   "手挡一下光、呼吸乱一拍）。" % hits[0]}


def stop_check(line, branch="", story=False):
    """她不该有"停下/站住"的概念：他牵着她一直在走。

    只有代码给出 `pause`（标定过 flow、他真停了）时才可以停。
    回忆/故事/引号里的招牌原文不动。
    """
    if story or str(branch or "") == "lang":
        return {"ok": True, "how": "故事豁免"}
    if str(branch or "") == "pause":
        return {"ok": True, "how": "代码允许停"}
    raw = str(line or "")
    if any(k in raw for k in RECALL_MARKS):
        return {"ok": True, "how": "回忆豁免"}
    s = _outside_quotes(raw)
    hits = [w for w in STOP_WORDS if w in s]
    hits = [w for w in hits if not any(w != o and w in o for o in hits)]
    if not hits:
        return {"ok": True, "how": "没说要停"}
    return {"ok": False, "how": "说了停:%s" % "/".join(hits[:2]),
            "why": "别说「%s」：他牵着你一直在走，你也在走。可以写腿沉、跟不上、慢半步，"
                   "但别说挪不动/走不动/站住；只有代码给出「他停了」才可以停。" % hits[0]}


# 「进去」假设了里面有空间、也假设他会带你进；改成「过去」，只表达想往那边走。
# 只抓"想进去"的意向句；「先不进去」「放进去」这类不算（不误伤否定和实义动作）。
ENTER_WORDS = ("进去看看", "进去吧", "带我进去", "进店", "进去逛", "进去坐",
               "进去转", "进去待")


def entry_check(line, story=False):
    if story:
        return {"ok": True, "how": "故事豁免"}
    raw = str(line or "")
    if any(k in raw for k in RECALL_MARKS):
        return {"ok": True, "how": "回忆豁免"}
    s = _outside_quotes(raw)
    hits = [w for w in ENTER_WORDS if w in s]
    hits = [w for w in hits if not any(w != o and w in o for o in hits)]
    if not hits:
        return {"ok": True, "how": "没说进去"}
    return {"ok": False, "how": "说了进去:%s" % "/".join(hits[:2]),
            "why": "别说「%s」：你不知道里面什么样，也不知道他会不会进。"
                   "改成「过去看看」「往那边走」。" % hits[0]}


# 视觉计数不可靠：不写"三个字/两行/三只"，改成"几个字/一串/一小排"。
# 只抓 2 以上的数，不误伤"一个/一行"这种自然量词。
_COUNT_NUM = r"(?:[2-9]|[1-9]\d+|[二两三四五六七八九十百]+)"
_COUNT_NOUN = (r"(?:小字|字|行|招牌|灯笼|灯|人|(?:小)?狗|(?:小)?猫|(?:小)?鸟|"
               r"(?:小)?鸽子|小东西|花瓣|叶子)")
COUNT_RE = re.compile(_COUNT_NUM + r"\s*(?:个|只|条|块|盏|根|张|片|串|排|行)?\s*" + _COUNT_NOUN)
COUNT_WORD_RE = re.compile(_COUNT_NUM + r"\s*个?\s*[A-Za-z]{1,12}")


def count_check(line, story=False):
    if story:
        return {"ok": True, "how": "故事豁免"}
    raw = str(line or "")
    if any(k in raw for k in RECALL_MARKS):
        return {"ok": True, "how": "回忆豁免"}
    s = _outside_quotes(raw)
    m = COUNT_RE.search(s) or COUNT_WORD_RE.search(s)
    if not m:
        return {"ok": True, "how": "没数数量"}
    return {"ok": False, "how": "数了数量:%s" % m.group(0),
            "why": "别数字数/行数/个数（「%s」）：看不清就说「几个字」「一串」「一小排」，"
                   "不确定就不说数量。" % m.group(0)}


# 脚感不靠谱：不能从脚底软硬推出"踩到/踩着什么东西"。
STEP_WORDS = ("踩到", "踩在", "踩着", "踩上", "一踩", "踩了", "踩下去", "踩出")
STEP_SURFACE = ("垫子", "毯子", "花瓣", "石子", "石头", "水坑", "草地",
                "地砖", "木板", "沙子", "泥土")
_STEP_NEAR_RE = re.compile(
    r"(?:脚底|脚下|踩)[^。！？\n]{0,12}(?:%s)" % "|".join(STEP_SURFACE))
_STEP_CHANGE_RE = re.compile(
    r"(?:脚底|脚下)[^。！？\n]{0,8}(?:换成|变成)[^。！？\n]{0,4}(?:硬|软|滑)")
_STEP_SOFT_RE = re.compile(
    r"(?:脚底|脚下)[^。！？\n]{0,6}(?:软得|硬得|软塌塌|硬邦邦)")


def step_check(line, story=False):
    """脚感只写平衡，不写脚下是什么。

    可以从脚底写"软了一下、滑了一下、没踩稳"，但不能因此认定
    "踩到/踩着垫子、花瓣、石头"。这种推断会变成下一格的既定事实，
    整条线程都建在一个不可靠的感觉上。
    """
    if story:
        return {"ok": True, "how": "故事豁免"}
    raw = str(line or "")
    if any(k in raw for k in RECALL_MARKS):
        return {"ok": True, "how": "回忆豁免"}
    s = _outside_quotes(raw)
    hit = next((w for w in STEP_WORDS if w in s), "")
    m = _STEP_NEAR_RE.search(s) or _STEP_CHANGE_RE.search(s) or _STEP_SOFT_RE.search(s)
    if hit or m:
        token = hit or m.group(0)
        return {"ok": False, "how": "踩/脚感:%s" % token[:20],
                "why": "脚感只写平衡：脚下一软、滑了一下、没踩稳；"
                       "别写「踩到什么」，也别给脚下安材质和名字（%s）。" % token[:20]}
    return {"ok": True, "how": "没靠脚感编东西"}


# 看一个地方的线程：最后一格必须走，不能又约"再看一会"。
MORE_LOOK_WORDS = ("再看", "多看", "看一会", "看一会儿", "看会儿",
                   "看两眼", "瞄一眼", "再瞅", "多瞅",
                   "再逛", "多逛", "逛一会", "再待", "多待", "待一会")


def place_close_check(line, story=False):
    if story:
        return {"ok": True, "how": "故事豁免"}
    s = _outside_quotes(str(line or ""))
    hits = [w for w in MORE_LOOK_WORDS if w in s]
    if not hits:
        return {"ok": True, "how": "收尾没有再看"}
    return {"ok": False, "how": "最后一格还在看:%s" % hits[0],
            "why": "这是最后一格：看够了，该走了。用一句离开收尾"
                   "（看够了，你带我走 / 不看了，我们走吧 / 你牵我往前走），"
                   "别再约「%s」。" % hits[0]}


def heart_counts(line, mood):
    """好感只在这三种情况下加：

    1. 台词里**明确夸了画面**（"真好看""没白来""你倒是挺会挑地方"）；
    2. 这一格开心；
    3. 这一格害羞，或者感动。

    只是描述景物、只是身体松了一下（"眼睛一下就松了"），不算——
    实测那句"两边的墙都爬满了绿的，我眼睛一下就松了"就是这么白拿了一笔的。
    读招牌/念字也不算（那是"想要"，不是"觉得好看"）。
    """
    line = line or ""
    if (mood or "") in MOOD_SHOWN:
        return True
    if any(w in line for w in READING_WORDS):
        return False
    return any(w in line for w in PRAISE_WORDS)

# 拐进回忆是契约允许的正经动作，不该被"必须接住上一句"判死。
RECALL_MARKS = ("以前", "小时候", "记得", "想起", "那年", "上回", "有次", "后来", "当时")


def ends_on_him(line, call=""):
    """A4：每一轮的落点要在他身上。

    画面只是由头。说完画面，最后一句要回到"你"——问他、要他、怪他、谢他。
    这条把"所有话都围绕我（玩家）"变成可判定的：只查**最后一句**，
    因为落点就是最后一句；前一句写景、写自己都不管。
    """
    parts = [x.strip() for x in re.split(r"[。！？…\n]+", str(line or "")) if x.strip()]
    if not parts:
        return {"ok": False, "last": "", "why": "没有句子"}
    last = parts[-1]
    if any(w in last for w in SECOND_PERSON) or (call and str(call) in last):
        return {"ok": True, "last": last}
    return {"ok": False, "last": last,
            "why": "最后一句是「%s」，落点停在画面上了" % last}


def check_reference(line, ref, prev_lines):
    """A1 验收：她这一句有没有接住自己上一句。

    两条合法路径：
    - 引用字段是她上（上）一句里的实词，或一个动作（追问/收回/改口/自嘲/补完）；
    - **或者**这一句自己就跟上一句共用了一个实词。

    第二条是兜底，也是这版修的坑：原来"没写引用"在第一行就直接判失败，
    连句子有没有真的接上都懒得看——模型偶尔漏填一个字段，整格就被吞了
    （日志里"两次都不过：没接住上一句 / 引:没写引用"就是这么来的）。
    现在顺序反过来：先看字面，再看字段；两边都不成立才算断。
    """
    ref = str(ref or "").strip().strip("“”\"'「」")
    prev = [str(x or "") for x in (prev_lines or [])[:2]]
    if not prev:
        return {"ok": True, "how": "首句", "ref": ref}
    has_ref = bool(ref) and ref not in ("无", "—", "-", "没有")
    joined = "".join(prev)

    # 拐回忆：这一句本来就是要从眼前拐走，不接上一句是对的。
    # 不加这条，实测里那句"墙上爬了那么多绿叶子，我小时候住的那种老街上也这样"
    # 会被判成"没接住"，整格吞掉。
    if any(k in line for k in RECALL_MARKS):
        return {"ok": True, "how": "拐回忆", "ref": ref}

    if has_ref:
        if any(a in ref for a in ACTION_REFS):
            return {"ok": True, "how": "动作", "ref": ref}
        if len(ref) == 1 and ref in joined and ref in line:
            return {"ok": True, "how": "原词", "ref": ref}
        if len(ref) >= 2 and (ref in joined or _norm(ref) in _norm(joined)):
            return {"ok": True, "how": "原词", "ref": ref}
        if len(ref) >= 2 and _bigrams(ref) & _bigrams(joined):
            return {"ok": True, "how": "近义", "ref": ref}

    # 兜底：这一句自己有没有跟她上一句共用一个**实词**。
    # 「了一」「的时」这种虚字对不算——原来不分虚实，日志里出现过"引:句内重合:了一"，
    # 等于 A1 形同虚设。
    shared = {b for b in _bigrams(line) & _bigrams(joined)
              if b not in _STOP_BIGRAMS and not (set(b) & set(_EMPTY_CHARS))}
    if shared:
        word = next(iter(shared))
        return {"ok": True,
                "how": ("句内重合:%s" % word) if has_ref else ("推断:%s" % word),
                "ref": ref}
    return {"ok": False, "how": ("引用对不上" if has_ref else "没写引用"), "ref": ref}


# ---------- 输出解析 ----------

_TAG_RE = re.compile(r"^【\s*(说|態|态|据|據)\s*】\s*(.*)$")
_TAG_SPLIT_RE = re.compile(r"(【\s*(?:说|態|态|据|據)\s*】)")


def split_output(raw):
    """拆【说】【态】【据】。标签缺失时整段当台词（兼容老格式）。

    不能按行切：模型偶尔把三行挤成一行——
    `【说】… 【态】情绪=开心… 【据】…`。按行切时只有第一个标签会被识别，
    后面整段都进了台词，情绪/引用/心动/记账全部丢失（日志里会显示平静）。
    所以先按标签切，换行只是普通内容。
    """
    text = (raw or "").replace("\r", "").strip()
    if not text:
        return {"say": "", "state": "", "evidence": "", "tagged": False}
    parts = _TAG_SPLIT_RE.split(text)
    if len(parts) == 1:
        return {"say": text, "state": "", "evidence": "", "tagged": False}
    say, state, evi = [], [], []
    cur = None
    for seg in parts:
        m = _TAG_RE.match(seg.strip())
        if m:
            tag = m.group(1)
            if tag == "说":
                cur = "say"
            elif tag in ("态", "態"):
                cur = "state"
            else:
                cur = "evi"
            body = m.group(2).strip()
            if body:
                {"say": say, "state": state, "evi": evi}[cur].append(body)
            continue
        seg = seg.strip()
        if not seg:
            continue
        if cur == "say":
            say.append(seg)
        elif cur == "state":
            state.append(seg)
        elif cur == "evi":
            evi.append(seg)
    return {"say": "\n".join(say).strip(), "state": "".join(state).strip(),
            "evidence": "".join(evi).strip(), "tagged": True}


def parse_state(state_text):
    """【态】情绪=平静｜空间=室外｜引用=车筐｜步=改｜记账=+问"…" """
    out = {"mood": "平静", "space": "", "ref": "", "step": "", "account": "",
           "heart": False, "pending_add": [], "pending_clear": []}
    if not state_text:
        return out
    # 字段分隔符模型会换着用：｜ | 丨 ，甚至逗号。先按竖线切；
    # 如果某一段里塞了两个以上的 key（"情绪=平静，空间=室外"），再按逗号细分。
    parts = []
    for seg in re.split(r"[｜|丨]", state_text):
        keys = re.findall(r"(?:情绪|心情|空间|地方|引用|接住|接|步|推进|心动|好看|记账|账|pending)\s*[=:：＝]",
                          seg, re.I)
        parts.extend(re.split(r"[，,；;]", seg) if len(keys) >= 2 else [seg])
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([^=:：＝﹕]{1,6})\s*[=:：＝﹕]\s*(.*)$", part)
        if not m:
            continue
        k = m.group(1).strip()
        # 值只取第一个词：模型偶尔用空格分行，"平静 引用：手冷"会整段塞进 mood
        v = re.split(r"[\s｜|丨，,；;]", m.group(2).strip())[0].strip()
        if k in ("情绪", "心情"):
            out["mood"] = v
        elif k in ("空间", "地方"):
            out["space"] = v
        elif k in ("引用", "接", "接住"):
            out["ref"] = v
        elif k in ("步", "推进"):
            out["step"] = v
        elif k in ("心动", "好看"):
            out["heart"] = v.strip().startswith(("是", "对", "有", "y", "Y", "1"))
        elif k in ("记账", "账", "pending"):
            out["account"] = v
    # 兜底：有的模型用空格分行（"情绪：平静 引用：手冷"），上面的切分会整行漏掉。
    # 只补最要命的那一个字段——引用；它空着，A1 就只能靠字面重合猜。
    if not out["ref"]:
        m = re.search(r"引用\s*[=:：＝]\s*([^\s｜|丨，,；;]{1,12})", state_text)
        if m:
            out["ref"] = m.group(1)
    acc = out["account"]
    if acc:
        for kind, txt in re.findall(r"[+＋]\s*([问求账記记])\s*[“\"「']([^”\"」']{1,30})", acc):
            kk = {"问": "question", "求": "request", "账": "account", "記": "account",
                  "记": "account"}[kind]
            out["pending_add"].append((kk, txt.strip()))
        for txt in re.findall(r"清\s*[“\"「']([^”\"」']{1,30})", acc):
            out["pending_clear"].append(txt.strip())
        if re.search(r"清\s*(?:全部|所有|都清)", acc):
            out["pending_clear"].append("全部")
    return out


# ---------- 处境 ----------


def _where(rect, with_y=True):
    if not rect:
        return ""
    cx = rect[0] + rect[2] / 2.0
    cy = rect[1] + rect[3] / 2.0
    x = "左边" if cx < 0.34 else ("右边" if cx > 0.66 else "中间")
    if not with_y:
        return x
    y = "上面" if cy < 0.38 else ("下面" if cy > 0.66 else "当中")
    return "%s偏%s" % (x, y) if x != "中间" and y != "当中" else ("%s%s" % (y, "" if x == "中间" else x))


def situation_lines(sig, decision, calibration_note=""):
    """把代码知道的信号翻成「她身上的感觉」，**不许翻成世界事实**。

    这是契约里唯一合法的那条世界通道：光打在身上、空间压在身上。所以措辞全部
    以「我/身上/眼睛/脚」为主语，不出现「这里有/前面是」这种断言。
    """
    if not sig:
        return []
    out = []
    branch = decision.get("branch") or ""
    ev = decision.get("events") or []
    t = sig.get("text") or {}
    b = sig.get("bubble") or {}
    ring = sig.get("ring") or {}

    if "bubble" in ev and b.get("rect"):
        out.append("旁边有人说了句话（%s），不是冲你来的。" % _where(b["rect"]))
    elif "sign" in ev and t.get("rect"):
        out.append("有一块字（%s）清楚到你能读，你眼睛自己贴过去了。" % _where(t["rect"]))
    if "revisit" in ev:
        out.append("这块地方你先前走过，你脚下先认出来了。")
    if "transit" in ev:
        # 只给事实，不要给现成句。原来写"你眼睛还没跟上"，
        # 模型会直接搬进台词，灯串/风铃/招牌每格都"眼睛还没跟上"。
        out.append("亮度跳了一档。")
    if "desire" in ev:
        out.append("有东西压得很近，你身上先觉得挤。")
    if "pause" in ev:
        out.append("他不动了，你跟着停住。")
    if ring.get("label") == "贴身" and (ring.get("conf") or 0) >= 0.6:
        out.append("有东西贴到你身上，近到挡光。")
    elif ring.get("label") == "远处" and (ring.get("conf") or 0) >= 0.7:
        out.append("眼前这一片离你很远，你听不见那边。")
    op = sig.get("openness") or 0
    if abs(op) >= 30 and branch in ("aesthetic", "transit", "lang", "follow"):
        out.append("脚下比头顶空。" if op > 0 else "头顶比脚下空。")
    if calibration_note:
        out.append(calibration_note)
    return out


def calibration_hint(router):
    if getattr(router, "calibrated", False):
        return ""
    return "（flow 地板还没标定过，所以这一格不许判断他停没停。）"


def handoff_block(prev_lines):
    """A1 的提示部分：把她上一句原文摆出来，并写死要求。"""
    lines = [str(x or "").strip() for x in (prev_lines or []) if str(x or "").strip()]
    if not lines:
        return ""
    last = lines[0]
    return ("你上一句是：「%s」\n"
            "这一句必须接住它：要么带上里面的一个词，要么对那句话做一个动作"
            "（追问/改口/自嘲/补完）。【态】里的「引用」就写你接住的那个词。\n"
            "不要重新开头。你在跟他聊天，不是在给每一格配一句字幕。" % last)


def pending_pressure(router):
    """有问过没答的，就别再问——否则每句都变成讨债。"""
    asked = [p["text"] for p in (router.pending or []) if p.get("kind") == "question"]
    if not asked:
        return ""
    return ("这些你已经问过了，他没答：%s。别再问同一件事，也别追着他要回答。"
            % "、".join(asked[:3]))


# ---------- 拼装 ----------

def build_blocks(cfg, pm, router=None, decision=None, sig=None, recent_lines=None,
                 system_memory="", force=False, shape="", story=False, story_exit=False):
    """把契约、分支、信号、线程、记账、接住、记忆拼成一堆待装填的块。"""
    from prompts import parse_sections, render  # 局部导入，避免循环

    raw_contract = (pm.get("contract") or "").strip()
    csec = parse_sections(raw_contract)
    full = (csec.get("full") or "").strip()
    if not full and "=== full" not in raw_contract:
        full = raw_contract
    life = (pm.get("life") or "").strip()
    # 红线单独抽出来：模板在**最前面**和**输出之前**各放一次。
    # 模型对开头和结尾最敏感，中间那一大段契约反而容易被路过的分支说明冲淡。
    rules_block = (csec.get("rules_story" if story else "rules") or "").strip()

    if story:
        # 故事格没有画面，整段"看画面"的契约都不适用，只留人物本身。
        contract_text = ("## 你是谁\n" + life) if life else ""
    else:
        contract_text = full + (("\n\n## 你是谁\n" + life) if life else "")

    branch = (decision or {}).get("branch") or ("follow" if recent_lines else "lang")
    sections = parse_sections(pm.get("branches") or "")
    # 故事线程的续锚用单独一段：通用的 follow 里写着"把话拐到你自己身上"，
    # 模型会拿它当许可证，讲着老板添饭突然拐去另一件叉烧。
    if story and branch == "follow":
        branch_text = (sections.get("follow_story") or sections.get("follow") or "").strip()
    else:
        branch_text = (sections.get(branch) or "").strip()
    if branch_text:
        branch_text = "【这一格为什么开口】\n" + branch_text
    if (decision or {}).get("wish") and branch_text:
        branch_text += ("\n（这一格可以顺口说一句：想过去看看。"
                        "对他说，不等他答应；一句就够，别展开，也别描述那边有什么。）")
    # 看一个地方的最后一格：通用的 follow 里写着"把话拐到自己身上、回忆可以编"，
    # 模型会拿它当许可证，在"该走了"这一格突然开一段新的回忆。这里把分支任务换掉。
    from router import PLACE_BRANCHES as _PLACE_BRANCHES  # 局部导入，避免循环
    place_last = bool(
        router is not None and router.thread and not story
        and int(router.thread.get("remaining") or 0) <= 1
        and (router.thread.get("branch") or "") in _PLACE_BRANCHES)
    if place_last:
        branch_text = ("【这一格为什么开口】\n"
                       "这是看一个地方的最后一格：看够了，该走了。"
                       "说一句离开，话落在他身上；别再描述那里，别再约下一眼，"
                       "也别拐去讲自己的事。")

    # 故事模式：不告诉她画面里有什么，也不要求接上一句。
    # 否则"有一块字清楚到能读"会立刻把她拽回画面，故事永远开不了头。
    lines = [] if story else situation_lines(
        sig, decision or {}, calibration_hint(router) if router is not None else "")
    if lines:
        signal_block = render(pm.get("signals") or "", {
            "signal_lines": "\n".join("- " + x for x in lines)}).strip()
    else:
        signal_block = ""

    thread_block = ""
    if router is not None and router.thread:
        tb = render(pm.get("thread") or "", {"thread_line": router.thread_block()}).strip()
        # 故事格不显示 self_ask：它来自上一个画面话题的待答问题，
        # 显示出来就会变成故事的下一段（实测"叉烧真有那么厉害"就是这么进来的）。
        if router.thread.get("self_ask") and not story and not story_exit:
            tb += "\n这些是你问自己、他不用答的：%s" % "、".join(
                router.thread["self_ask"][:2])
        thread_block = tb

    pending_block = ""
    if router is not None and router.pending and not story and not story_exit:
        pending_block = render(pm.get("pending") or "",
                               {"pending_line": router.pending_block()}).strip()
        press = pending_pressure(router)
        if press:
            pending_block += "\n" + press

    # 接住上一句 / 换话题 / 从故事里出来。
    # 抢占格与故事出口都不能发"必须接住上一句"：
    # - 抢占：新事件把你拽走，硬接会挤出缝合句；
    # - 故事出口：上一句是故事收尾（"所以现在拎东西，我都把袋口攥紧"），
    #   再要求 A1，模型就会把故事里的袋口/橘子带到眼前，还顺手攥出手汗。
    preempt = ((decision or {}).get("action") == "抢占")
    handoff_text = ""
    if story_exit:
        handoff_text = ("【回到眼前】\n刚才那段以前的事已经讲完了。这一格回到眼前："
                        "不要接着故事里的东西和动作说，也不要把手里拎的、吃过的、买过的带到现在。\n"
                        "从眼前新出现的东西起手；【态】里的「引用」填「回到眼前」。")
    elif not story:
        hsec = parse_sections(pm.get("handoff") or "")
        if preempt:
            handoff_text = (hsec.get("preempt") or "").strip()
            if not handoff_text:
                handoff_text = ("【这一格换话题】新出现的东西把你拽走了。不用接住上一句，"
                                "也不要把上一句的后半截缝进来；【态】里的「引用」填「换话题」。")
        else:
            handoff = handoff_block(recent_lines)
            if handoff:
                tmpl = hsec.get("normal") or (pm.get("handoff") or "")
                handoff_text = render(tmpl, {"handoff_line": handoff}).strip()

    silence_key = str((cfg.get("behavior") or {}).get("silence") or "normal").lower()
    ssec = parse_sections(pm.get("silence") or "")
    silence_block = (ssec.get(silence_key) or ssec.get("normal") or "").strip()

    shape_block = ("这一格可以从这儿起手（也可以不用）：%s" % shape).strip() if shape else ""
    force_block = (pm.get("force") or "").strip() if force else ""

    # 风格分两段：look（先看画面哪里）和 talk（怎么说）。
    # 故事格没有画面，所以只取 talk——原来整段照发，"先说你被什么打到"直接和"不用看画面"打架。
    ssec = parse_sections(pm.get("aesthetic") or "")
    if ssec.get("talk"):
        # "从什么起手"只在事件格给：
        # 读字/气泡/过渡/靠近/回访有明确引子，需要先"被什么打到"；
        # 接话格和故事格是从上一句/回忆起手，再塞一句"先看画面"就是两个方向抢方向盘。
        ev = ("sign", "bubble", "transit", "pause", "aesthetic", "desire", "revisit")
        br = ((decision or {}).get("branch") or "")
        look = "" if (story or br not in ev) else (ssec.get("look", "").strip() + "\n\n")
        style_block = (look + ssec["talk"]).strip()
    else:
        style_block = (pm.get("aesthetic") or "").strip()
    call = ((cfg.get("persona") or {}).get("call_player") or "").strip()
    if call and call != "你" and (pm.get("call") or "").strip():
        used = any(call in str(x or "") for x in (recent_lines or [])[:3])
        csec = parse_sections(pm.get("call_state") or "")
        call_note = render(pm.get("call") or "", {
            "call": call,
            "call_state": (csec.get("used" if used else "unused") or "").strip(),
        }).strip()
        if call_note:
            style_block += "\n" + call_note

    # 记忆说明分三段：故事中 / 故事刚讲完 / 普通
    msec = parse_sections(pm.get("memory_rules") or "")
    mkey = "story" if story else ("after_story" if story_exit else "normal")
    rules = (msec.get(mkey) or msec.get("normal") or
             (pm.get("memory_rules") or "")).strip()
    mem = (system_memory or "").strip()
    if mem and rules:
        mem = mem + "\n\n" + rules

    # 角色页里的"语气"以前从来没进过提示词（改了没反应）。现在接上。
    tone = ((cfg.get("persona") or {}).get("tone") or "").strip()
    if tone:
        style_block += "\n语气：%s" % tone

    extra = (pm.get("extra") or "").strip()
    if extra:
        style_block += "\n" + extra

    story_note = ""
    if story:
        # 放在最前面：模型对开头两行的权重最高。
        # 只写在分支块里（在契约之后）压不住"刚才在聊的那件事"。
        story_note = ("（这一格没有新画面，也不用接上一句。讲一件**最近**的事——"
                      "前几天逛的街、上次去哪儿、吃到的一顿。跟眼前、跟刚才聊的都没关系。"
                      "哪天、在哪、手里拿着什么，说具体。别报年龄。）").replace("**", "")
    elif story_exit:
        # 故事刚收尾：模型最后一条 user 消息和开头两行都要站在"回到眼前"这边，
        # 否则它会顺手把故事里的袋口/橘子拿到现在来。
        story_note = ("（刚才那段以前的事已经讲完了。这一格回到眼前：不要接着故事里的东西和动作说；"
                      "从眼前新出现的东西起手。）")
    return {
        "story_note": story_note,
        "rules_block": rules_block,
        "contract_block": contract_text,
        "branch_block": branch_text,
        "signal_block": signal_block,
        "thread_block": thread_block,
        "pending_block": pending_block,
        "handoff_block": handoff_text,
        "memory": mem,
        "style_block": style_block,
        "silence_block": silence_block,
        "shape_block": shape_block,
        "force_block": force_block,
    }

