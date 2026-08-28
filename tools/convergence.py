"""研究任务收敛判定。

收尾同时要求：
1. 检索面和深读量达到客观下限；
2. 最近连续若干个不同样本都没有新增或修正；
3. 没有尚未通过成功重试解决的媒体理解失败。

报告行数不参与判定，归纳、压缩和重构不会被惩罚。
"""


def research_converged(notes_read, search_rounds, consecutive_no_new=0,
                       unresolved_media=0, min_notes=5, min_searches=2,
                       min_no_new=5):
    if (search_rounds or 0) < min_searches:
        return False
    if (notes_read or 0) < min_notes:
        return False
    if (consecutive_no_new or 0) < min_no_new:
        return False
    if (unresolved_media or 0) > 0:
        return False
    return True
