from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ResearchRoute:
    """A deterministic research route shared by planning and execution.

    The language model may decide how to arrange nodes, but it must not decide
    whether a normal web task is scholarly research.  Keeping that distinction
    deterministic prevents words such as ``研究`` from routing finance, policy,
    product, or news requests to Google Scholar.
    """

    domain: str
    domain_label: str
    mode: str
    preferred_sources: tuple[str, ...]
    query_facets: tuple[str, ...]
    high_stakes: bool = False
    requires_current_data: bool = False
    guidance: str = ""

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["preferred_sources"] = list(self.preferred_sources)
        value["query_facets"] = list(self.query_facets)
        return value


class ResearchRoutingService:
    """Classify arbitrary research tasks and select appropriate source families."""

    _academic = re.compile(
        r"学术|文献|论文|综述|期刊|会议论文|参考文献|引用|引文|DOI|"
        r"研究现状|研究进展|系统评价|元分析|"
        r"systematic\s+review|literature\s+review|scoping\s+review|meta[-\s]?analysis|"
        r"review\s+article|research\s+paper|journal\s+article|conference\s+paper|"
        r"^\s*(?:please\s+)?review\b|\bpapers?\b|\bcitations?\b|\breferences?\b",
        re.I,
    )
    _research_action = re.compile(
        r"联网|网页|网站|最新|近期|新闻|资料|检索|搜索|查找|查询|调查|调研|"
        r"了解|比较|推荐|建议|趋势|前景|行情|"
        r"\bonline\b|\bweb\b|search|research|investigate|latest|news|recommend",
        re.I,
    )
    _domains: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        (
            "finance_markets",
            "证券市场研究",
            re.compile(
                r"A股|股票|个股|板块|股价|涨停|大涨|牛股|证券|沪深|上证|深证|"
                r"创业板|科创板|北交所|港股|美股|ETF|基金|行业轮动|资金流向|"
                r"市盈率|市净率|估值|财报|基本面|上市公司|"
                r"stock|equity|share\s+price|securities|portfolio|valuation|earnings",
                re.I,
            ),
        ),
        (
            "news_current",
            "新闻与时事",
            re.compile(
                r"新闻|时事|热点|舆情|刚刚|今天|本周|最新消息|事件进展|"
                r"breaking\s+news|current\s+events?|news\s+update",
                re.I,
            ),
        ),
        (
            "policy_government",
            "政策与公共信息",
            re.compile(
                r"政策|法规|条例|办法|通知|规划纲要|政府工作报告|补贴|申报|"
                r"国务院|发改委|工信部|教育部|财政部|国家标准|行业标准|"
                r"policy|regulation|government|public\s+policy",
                re.I,
            ),
        ),
        (
            "legal",
            "法律与合规",
            re.compile(
                r"法律|法条|司法解释|判例|裁判文书|诉讼|合同纠纷|合规|律师|"
                r"legal|law|statute|case\s+law|compliance",
                re.I,
            ),
        ),
        (
            "health_medical",
            "健康与医疗",
            re.compile(
                r"疾病|症状|诊断|治疗|药物|用药|医院|临床|患者|健康|医学|"
                r"medical|health|clinical|diagnosis|treatment|medicine",
                re.I,
            ),
        ),
        (
            "product_comparison",
            "产品与消费决策",
            re.compile(
                r"产品对比|选购|购买|性价比|价格比较|哪款|哪个好|推荐.{0,8}(?:手机|电脑|相机|汽车)|"
                r"比较.{0,20}(?:手机|电脑|相机|汽车|产品)|"
                r"product\s+comparison|buying\s+guide|which\s+.*\s+buy",
                re.I,
            ),
        ),
        (
            "travel_local",
            "旅行与本地信息",
            re.compile(
                r"旅行|旅游|行程|景点|酒店|机票|高铁|餐厅|天气|签证|当地|"
                r"travel|trip|hotel|flight|restaurant|visa|weather",
                re.I,
            ),
        ),
        (
            "education",
            "教育与教学",
            re.compile(
                r"教育|教学|课程|课堂|备课|教师|学生|作业|试题|学情|助教|助学|"
                r"education|teaching|learning|curriculum|student",
                re.I,
            ),
        ),
        (
            "technology",
            "技术与产业",
            re.compile(
                r"技术|软件|硬件|开源|框架|API|算法|模型|芯片|人工智能|量子计算|"
                r"产业|市场规模|竞争格局|technology|software|hardware|framework|"
                r"artificial\s+intelligence|market\s+size|industry",
                re.I,
            ),
        ),
    )

    _source_profiles: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
        "finance_markets": (
            (
                "证券交易所与巨潮资讯公告",
                "证监会及政府部门",
                "上市公司定期报告",
                "可信金融数据与财经媒体",
            ),
            ("行业景气与政策", "公司基本面与估值", "资金与市场行为", "风险与公告"),
            "先核对最新行情日期和价格约束，再以公告、财报和监管信息验证候选；"
            "区分事实、推断和情景，不承诺收益或使用“必涨”结论。",
        ),
        "news_current": (
            ("事件一手来源", "权威新闻机构", "当事方公开声明", "事实核查来源"),
            ("事件时间线", "各方声明", "最新进展", "争议与核验"),
            "按事件发生时间而非仅按发布时间组织证据，并交叉核对关键事实。",
        ),
        "policy_government": (
            ("政府与主管部门官网", "法规政策数据库", "官方解读", "权威行业机构"),
            ("政策原文", "适用对象", "生效时间", "实施影响"),
            "优先引用政策原文，明确发布机关、生效日期、适用范围和当前有效性。",
        ),
        "legal": (
            ("国家法律法规数据库", "法院与监管机关", "裁判文书", "权威法律释义"),
            ("现行法条", "司法解释与案例", "管辖与时效", "合规风险"),
            "核对法域、版本和生效状态；输出一般信息与风险提示，不替代专业法律意见。",
        ),
        "health_medical": (
            ("卫生主管部门", "临床指南", "医学数据库", "权威医疗机构"),
            ("指南共识", "证据等级", "适应证与禁忌", "风险与就医建议"),
            "优先指南和高等级证据，区分科普信息与诊疗建议，并提示必要的专业就医。",
        ),
        "product_comparison": (
            ("品牌官方规格", "独立评测", "主流零售价格", "用户长期反馈"),
            ("规格与兼容性", "实测表现", "当前价格", "售后与长期成本"),
            "基于使用场景和预算比较，标注价格日期，区分厂商宣传与独立实测。",
        ),
        "travel_local": (
            ("政府与场馆官网", "交通运营方", "地图与天气服务", "可靠预订平台"),
            ("开放与交通", "天气与季节", "价格与预约", "安全与备选方案"),
            "核对出行日期、开放时间和实时交通，给出可调整的备选安排。",
        ),
        "education": (
            ("教育主管部门", "课程标准与教材", "学校官方资料", "可靠教学资源"),
            ("课程目标", "学习者特征", "教学活动", "评价与反馈"),
            "依据课程标准和学习目标组织资料，区分通用建议与具体学情证据。",
        ),
        "technology": (
            ("官方文档与标准", "项目仓库与发布说明", "权威产业报告", "可信技术媒体"),
            ("官方能力与限制", "版本与兼容性", "基准与案例", "生态与趋势"),
            "优先官方文档和当前版本信息，基准数据需说明测试条件。",
        ),
        "general_web": (
            ("官方一手来源", "权威机构", "可信媒体与报告", "可交叉验证来源"),
            ("背景与定义", "现状与数据", "不同观点", "风险与限制"),
            "优先一手和当前资料，对关键结论进行多来源交叉验证。",
        ),
    }

    _high_stakes_domains = {"finance_markets", "legal", "health_medical"}
    _current_domains = {
        "finance_markets",
        "news_current",
        "policy_government",
        "legal",
        "health_medical",
        "product_comparison",
        "travel_local",
        "technology",
    }

    def classify(self, task: str) -> ResearchRoute:
        text = " ".join(str(task or "").split())
        scholarly = bool(self._academic.search(text))
        domain = "general_web"
        domain_label = "通用网络研究"
        for candidate, label, pattern in self._domains:
            if pattern.search(text):
                domain, domain_label = candidate, label
                break

        if scholarly:
            sources = ("Google Scholar", "Crossref/DOI", "期刊与出版社官网", "学术数据库")
            facets = ("核心概念", "研究进展", "方法与评价", "应用与局限")
            guidance = (
                "使用学术检索并保留 DOI、作者、年份和出处；真实来源不足时如实披露，不得补造论文。"
            )
            if domain == "general_web":
                domain, domain_label = "academic_research", "学术研究"
            return ResearchRoute(
                domain=domain,
                domain_label=domain_label,
                mode="academic",
                preferred_sources=sources,
                query_facets=facets,
                high_stakes=domain in self._high_stakes_domains,
                requires_current_data=False,
                guidance=guidance,
            )

        sources, facets, guidance = self._source_profiles[domain]
        return ResearchRoute(
            domain=domain,
            domain_label=domain_label,
            mode="web",
            preferred_sources=sources,
            query_facets=facets,
            high_stakes=domain in self._high_stakes_domains,
            requires_current_data=domain in self._current_domains,
            guidance=guidance,
        )

    def should_research(self, task: str) -> bool:
        route = self.classify(task)
        return bool(
            self._research_action.search(str(task or ""))
            or route.mode == "academic"
            or route.requires_current_data
        )


research_routing_service = ResearchRoutingService()
