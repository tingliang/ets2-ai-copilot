# -*- coding: utf-8 -*-
"""
欧洲地区「趣事 / 特产 / 历史」知识库。

主动闲聊（M6）用：副驾在平稳行驶时主动找话题，讲一句当地的小知识，
内容比「芬兰很多树」这种泛泛而谈丰富得多。

匹配逻辑：城市名(英文，归一化) → 国家 → 取该国家的一条趣事。
没命中城市/国家时返回 None，由调用方走通用闲聊兜底。
"""
import random
import unicodedata

# 城市英文名(归一化: 小写、去空格/连字符) -> 国家 key
CITY_COUNTRY = {
    # 芬兰
    "helsinki": "finland", "turku": "finland", "tampere": "finland",
    "lahti": "finland", "jyvaskyla": "finland", "kuopio": "finland",
    "joensuu": "finland", "pori": "finland", "kotka": "finland",
    "kouvola": "finland", "loviisa": "finland", "naantali": "finland",
    # 瑞典
    "stockholm": "sweden", "goteborg": "sweden", "gothenburg": "sweden",
    "malmo": "sweden", "linkoping": "sweden", "orebro": "sweden",
    "vasteras": "sweden", "uppsala": "sweden", "karlskrona": "sweden",
    "kalmar": "sweden", "jonkoping": "sweden", "helsingborg": "sweden",
    "kapellskar": "sweden", "sodertalje": "sweden",
    # 挪威
    "oslo": "norway", "bergen": "norway", "stavanger": "norway",
    "kristiansand": "norway", "trondheim": "norway",
    # 丹麦
    "copenhagen": "denmark", "kobenhavn": "denmark", "esbjerg": "denmark",
    "gedser": "denmark", "frederikshavn": "denmark", "hirtshals": "denmark",
    "aalborg": "denmark", "alborg": "denmark", "odense": "denmark",
    # 德国
    "berlin": "germany", "hamburg": "germany", "munchen": "germany",
    "munich": "germany", "frankfurt": "germany", "koln": "germany",
    "cologne": "germany", "dusseldorf": "germany", "dresden": "germany",
    "leipzig": "germany", "stuttgart": "germany", "nurnberg": "germany",
    "bremen": "germany", "hannover": "germany", "rostock": "germany",
    "kiel": "germany", "erfurt": "germany", "kassel": "germany",
    "dortmund": "germany", "duisburg": "germany", "magdeburg": "germany",
    "mannheim": "germany", "osnabruck": "germany", "travemunde": "germany",
    # 法国
    "paris": "france", "lyon": "france", "marseille": "france",
    "lille": "france", "metz": "france", "strasbourg": "france",
    "dijon": "france", "reims": "france", "lehavre": "france",
    "calais": "france", "toulouse": "france", "bordeaux": "france",
    "montpellier": "france", "nantes": "france", "rennes": "france",
    "brest": "france",
    # 意大利
    "milano": "italy", "milan": "italy", "torino": "italy", "turin": "italy",
    "venezia": "italy", "venice": "italy", "verona": "italy",
    "bologna": "italy", "firenze": "italy", "florence": "italy",
    "roma": "italy", "rome": "italy", "napoli": "italy", "naples": "italy",
    "genova": "italy", "livorno": "italy", "pescara": "italy",
    "bari": "italy", "palermo": "italy", "messina": "italy",
    "catania": "italy", "cagliari": "italy", "olbia": "italy",
    # 波兰
    "warszawa": "poland", "warsaw": "poland", "krakow": "poland",
    "wroclaw": "poland", "poznan": "poland", "gdansk": "poland",
    "gdynia": "poland", "szczecin": "poland", "lodz": "poland",
    "katowice": "poland", "lublin": "poland", "bialystok": "poland",
    "olsztyn": "poland", "rzeszow": "poland",
    # 英国
    "london": "uk", "manchester": "uk", "liverpool": "uk",
    "newcastle": "uk", "edinburgh": "uk", "glasgow": "uk",
    "cardiff": "uk", "dover": "uk", "felixstowe": "uk", "grimsby": "uk",
    "harwich": "uk", "holyhead": "uk", "hull": "uk", "plymouth": "uk",
    "sheffield": "uk", "southampton": "uk",
    # 荷兰 / 比利时
    "amsterdam": "netherlands", "rotterdam": "netherlands",
    "groningen": "netherlands", "eindhoven": "netherlands", "utrecht": "netherlands",
    "bruxelles": "belgium", "brussels": "belgium", "antwerpen": "belgium",
    "antwerp": "belgium", "liege": "belgium",
    # 西班牙 / 葡萄牙
    "madrid": "spain", "barcelona": "spain", "valencia": "spain",
    "sevilla": "spain", "zaragoza": "spain", "bilbao": "spain",
    "acoruna": "spain", "gijon": "spain", "santander": "spain",
    "vigo": "spain", "malaga": "spain", "almeria": "spain",
    "lisboa": "portugal", "lisbon": "portugal", "porto": "portugal",
    # 奥地利 / 瑞士 / 捷克
    "wien": "austria", "vienna": "austria", "graz": "austria",
    "linz": "austria", "salzburg": "austria", "innsbruck": "austria",
    "klagenfurt": "austria", "zurich": "switzerland", "geneve": "switzerland",
    "geneva": "switzerland", "bern": "switzerland", "basel": "switzerland",
    "praha": "czech", "prague": "czech", "brno": "czech", "ostrava": "czech",
    # 罗马尼亚 / 保加利亚 / 土耳其
    "bucuresti": "romania", "bucharest": "romania", "clujnapoca": "romania",
    "timisoara": "romania", "iasi": "romania", "constanta": "romania",
    "sofia": "bulgaria", "varna": "bulgaria", "burgas": "bulgaria",
    "ruse": "bulgaria", "pleven": "bulgaria", "plovdiv": "bulgaria",
    "istanbul": "turkey", "edirne": "turkey", "tekirdag": "turkey",
    # 波罗的海
    "vilnius": "lithuania", "kaunas": "lithuania", "klaipeda": "lithuania",
    "riga": "latvia", "liepaja": "latvia", "ventspils": "latvia",
    "tallinn": "estonia", "tartu": "estonia", "narva": "estonia",
    # 斯洛伐克 / 匈牙利 / 斯洛文尼亚 / 克罗地亚
    "bratislava": "slovakia", "kosice": "slovakia", "zilina": "slovakia",
    "budapest": "hungary", "debrecen": "hungary", "szeged": "hungary",
    "ljubljana": "slovenia", "maribor": "slovenia", "koper": "slovenia",
    "zagreb": "croatia", "rijeka": "croatia", "split": "croatia",
    # 俄罗斯飞地
    "kaliningrad": "russia",
}

# 国家 -> 趣事列表（中文，一条讲一个小知识，适合口语化转述）
COUNTRY_NOTES = {
    "finland": [
        "芬兰号称千湖之国，其实有超过18万个湖，名副其实的水乡",
        "芬兰人是全世界喝咖啡最狠的，人均每天4杯起步，全靠咖啡续命",
        "芬兰全国的桑拿房比汽车还多，差不多300万间，桑拿就是他们的社交客厅",
        "圣诞老人的官方故乡就在芬兰拉普兰的罗瓦涅米，那里一年能收到几十万封孩子的信",
        "芬兰的森林覆盖率超过七成，所以你在路上看到满眼都是树，真的不夸张",
    ],
    "sweden": [
        "诺贝尔奖就是瑞典人诺贝尔设立的，每年在斯德哥尔摩颁奖",
        "宜家 IKEA 老家就在瑞典，全球人都睡过他们家的床",
        "瑞典人有个词叫 fika，就是每天雷打不动的咖啡加甜点时间，天塌了也要喝",
        "斯德哥尔摩建在14座岛上，人称北方威尼斯",
    ],
    "norway": [
        "挪威的峡湾是冰川刻出来的，美到被列入世界遗产，很多人专程开车来看",
        "挪威靠着北海石油成了全球最富的国家之一，国民幸福指数常年霸榜",
        "挪威三文鱼卖到全世界，你日料里那块刺身很可能就是挪威来的",
        "挪威人几乎生下来就会滑雪，有句谚语说挪威人踩着滑雪板出生",
    ],
    "denmark": [
        "乐高积木就来自丹麦，这个名字在丹麦语里是玩得好的意思",
        "丹麦是安徒生的故乡，小美人鱼雕像就坐在哥本哈根海边",
        "丹麦常年被评为世界上最幸福的国家之一，据说和他们讲究的舒适生活哲学有关",
    ],
    "germany": [
        "德国人做香肠能做出1500多种，面包更是超过300种，光靠吃就能逛很久",
        "德国高速部分路段真的不限速，是全球车迷的朝圣地",
        "奔驰、宝马、保时捷、奥迪、大众，全是一个国家生出来的，名副其实的汽车王国",
        "德国1516年就立了啤酒纯酿法，是全球最古老的食品安全法规之一",
        "柏林墙1989年倒塌，那堵墙曾经把一座城市活活分成两半",
    ],
    "france": [
        "法国是全世界游客最多的国家，每年接待将近一个亿人",
        "法国奶酪有350多种，据说一年每天换一种都吃不过来",
        "埃菲尔铁塔1889年就建成了，当年巴黎人还嫌它丑，现在成了地标",
        "法国人吃饭特别慢，平均每天要花两个多小时在餐桌上",
    ],
    "italy": [
        "披萨和意面的老家都在意大利，这里人对面的热爱是刻进骨子里的",
        "威尼斯整座城建在118个小岛上，出门全靠船，路上一条车都没有",
        "意大利拥有全世界最多的世界文化遗产，随便一座老城都有上千年历史",
        "罗马被称为永恒之城，城里随便踩块石头可能都是两千年前的",
    ],
    "poland": [
        "波兰是欧洲的琥珀之乡，波罗的海边随手能捡到琥珀原石",
        "钢琴诗人肖邦是波兰人，他去世后心脏被运回华沙安葬",
        "波兰饺子叫 pierogi，馅料从土豆奶酪到蓝莓都有，是国民美食",
        "波兰的维利奇卡盐矿整个挖在地下一百多米，里面还能建教堂，是世界遗产",
    ],
    "uk": [
        "英国是靠左行驶的国家，第一次开过来得先适应一会儿方向盘在右边",
        "英国人把下午茶当正经事，配上司康饼和红茶，一喝就是一百多年",
        "苏格兰威士忌世界闻名，苏格兰人管它叫生命之水",
    ],
    "netherlands": [
        "荷兰的风车和郁金香是招牌，春天整个国家变成一片花海",
        "阿姆斯特丹的运河密得像蜘蛛网，整座老城都是世界遗产",
        "荷兰人平均身高全球第一，据说跟他们爱喝牛奶有关系",
    ],
    "belgium": [
        "比利时巧克力是顶级货，全球巧克力老饕都认这块招牌",
        "布鲁塞尔是欧盟总部所在地，人称欧洲的首都",
        "比利时啤酒花样多到数不清，很多修道院自己酿啤酒",
    ],
    "spain": [
        "西班牙人晚饭特别晚，晚上九点十点才开吃，夜生活更长",
        "西班牙有午睡传统 siesta，中午很多小店会关门休息",
        "西班牙有个番茄节，整个小镇用番茄互相砸，砸得满街都是红的",
        "弗拉门戈舞和伊比利亚火腿，都是西班牙的名片",
    ],
    "portugal": [
        "葡式蛋挞的老家就在葡萄牙里斯本的贝伦区，全球蛋挞都认这个祖宗",
        "葡萄牙是大航海时代的先驱，麦哲伦、达伽马都从这里起航",
        "波尔图葡萄酒有名到一座城市都用它命名",
    ],
    "austria": [
        "维也纳是音乐之都，莫扎特、贝多芬、舒伯特都在这里生活过",
        "奥地利藏在阿尔卑斯山里，风景美得像明信片",
        "维也纳的金色大厅是全世界音乐家梦寐以求的舞台",
    ],
    "switzerland": [
        "瑞士是永久中立国，两次世界大战都没卷进去",
        "瑞士手表和瑞士巧克力，是全球公认的硬通货",
        "瑞士全境几乎都在阿尔卑斯山里，火车翻山越岭本身就是风景",
    ],
    "czech": [
        "布拉格被称为百塔之城，老城保存得极好，一步一景",
        "捷克是人均啤酒消费世界第一的国家，啤酒比矿泉水还便宜",
        "布拉格广场的天文钟有六百多年历史，每到整点就上演木偶秀",
    ],
    "romania": [
        "罗马尼亚的布朗城堡常被当成德古拉伯爵的老巢，吸血鬼传说的起点",
        "罗马尼亚是多瑙河三角洲的所在地，欧洲最大的湿地之一",
    ],
    "bulgaria": [
        "保加利亚的玫瑰谷产全世界最好的玫瑰精油，供应全球一半以上",
        "保加利亚酸奶有名到有一种益生菌就用国名命名",
    ],
    "turkey": [
        "伊斯坦布尔横跨欧亚两个大陆，一座城坐落在两大洲上",
        "土耳其烤肉和土耳其软糖，都是风靡全球的小吃",
    ],
    "lithuania": [
        "立陶宛的十字架山堆满了成千上万个十字架，是信仰的奇观",
    ],
    "latvia": [
        "拉脱维亚首都里加的老城，是新艺术风格建筑保存最完整的地方之一",
    ],
    "estonia": [
        "爱沙尼亚是数字国家，几乎一切政府服务都能在网上办，连投票都行",
        "塔林老城保存着中世纪风貌，像穿越回了八百年前",
    ],
    "slovakia": [
        "斯洛伐克有一半国土是山地，喀尔巴阡山脉贯穿全境",
    ],
    "hungary": [
        "布达佩斯被多瑙河一分为二，布达和佩斯原本是两座城",
        "匈牙利温泉资源丰富，泡温泉是他们延续了几百年的日常",
    ],
    "slovenia": [
        "斯洛文尼亚有欧洲最美的湖之一布莱德湖，湖心小岛上还有座教堂",
    ],
    "croatia": [
        "克罗地亚的亚得里亚海沿岸美得惊人，杜布罗夫尼克是权力的游戏取景地",
    ],
    "russia": [
        "加里宁格勒是俄罗斯的一块飞地，和本土不接壤，孤悬在波罗的海边",
    ],
}


def _norm(name: str) -> str:
    """归一化城市名：小写、去重音符号、去空格/连字符/下划线/点。

    ETS2 城市名带各种非英文字符（København、Wrocław、München、Rīga…），
    用 NFKD 把重音/变音符号拆掉后只保留 ASCII，就能和 CITY_COUNTRY 的 key 对上。
    """
    if not name:
        return ""
    s = name.lower()
    # NFKD 把带重音/变音的字符拆成「基础字母 + 组合符号」，过滤组合符号即可去重音
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    # ø/æ/ł/đ/ß 是独立字符，NFKD 不分解，手动转 ASCII
    s = s.replace("ø", "o").replace("æ", "ae").replace("ł", "l").replace("đ", "d").replace("ß", "ss")
    for ch in " -_.":
        s = s.replace(ch, "")
    return s


def pick_note(city: str, exclude: set[str] | None = None) -> str | None:
    """根据城市名随机取一条当地趣事。

    exclude：已讲过的趣事集合，避免重复；全讲完了则重置（返回任一条）。
    返回 None 表示没有匹配到该城市/国家，调用方应走通用闲聊兜底。
    """
    country = CITY_COUNTRY.get(_norm(city))
    if not country:
        return None
    notes = COUNTRY_NOTES.get(country)
    if not notes:
        return None
    if exclude is None:
        return random.choice(notes)
    # 优先选没讲过的
    fresh = [n for n in notes if n not in exclude]
    if not fresh:
        fresh = notes  # 该国家趣事讲完了，允许重复
    return random.choice(fresh)
