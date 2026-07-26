---
name: jsxgraph-math-visualization
description: Solve mathematics problems with explicit step-by-step derivations, rendered LaTeX formulas, and safe interactive JSXGraph charts. Use for algebra, functions, analytic geometry, calculus, vectors, trigonometry, probability distributions, numerical methods, or any question where a graph materially clarifies the mathematical relationship.
---

# JSXGraph 数学推导与图表

先完成数学推导，再绘制真正有解释价值的图；不得用图表替代证明。

## 回答流程

1. 明确已知量、未知量、定义域和必要假设。
2. 使用 `$$...$$` 表示独立公式，使用 `$...$` 表示行内公式。
3. 逐步写出等式变形、定理依据和关键中间结果，避免只给答案。
4. 检查结果的定义域、量纲、边界值或代回验证。
5. 当函数形状、几何关系、参数变化、导数/积分区域或概率分布适合可视化时，追加一个 `jsxgraph` JSON 代码块。

## JSXGraph 输出协议

只输出 JSON 数据，禁止输出 JavaScript、HTML、事件处理器或外部 URL。

```jsxgraph
{
  "title": "函数与切线",
  "boundingBox": [-5, 8, 5, -4],
  "axis": true,
  "objects": [
    {
      "type": "functiongraph",
      "expression": "x^2",
      "range": [-3, 3],
      "name": "f(x)=x^2",
      "color": "#1769c2"
    },
    {
      "type": "point",
      "coords": [1, 1],
      "name": "P",
      "color": "#d95f45"
    },
    {
      "type": "line",
      "points": [[0, -1], [2, 3]],
      "name": "切线",
      "color": "#168c83"
    }
  ]
}
```

支持的对象：

- `functiongraph`：`expression`，可选 `range`
- `curve`：`xExpression`、`yExpression`、`range`
- `point`：`coords`
- `line`、`segment`、`arrow`：`points` 中两个坐标
- `polygon`：`points` 中至少三个坐标
- `circle`：`center` 与正数 `radius`

表达式只使用数字、`x`、`t`、`pi`、`e`、四则运算、幂 `^`、括号，以及
`sin`、`cos`、`tan`、`asin`、`acos`、`atan`、`sqrt`、`abs`、`exp`、`log`、
`floor`、`ceil`、`round`、`min`、`max`。为图表设置紧凑且能展示关键特征的
`boundingBox: [left, top, right, bottom]`。

若问题不适合画图，只输出推导公式，不强行生成空洞图表。
