"""autosmartcut/nodes — StageNode 节点实现。

延迟导入以避免循环依赖。
"""

__all__ = [
    "L1Node",
    "L2aNode",
    "L2bNode",
    "L2cNode",
    "L2dNode",
    "L3Node",
]


def __getattr__(name: str):
    """延迟导入节点类。"""
    if name == "L1Node":
        from autosmartcut.nodes.l1.l1_node import L1Node
        return L1Node
    elif name == "L2aNode":
        from autosmartcut.nodes.l2.l2a_node import L2aNode
        return L2aNode
    elif name == "L2bNode":
        from autosmartcut.nodes.l2.l2b_node import L2bNode
        return L2bNode
    elif name == "L2cNode":
        from autosmartcut.nodes.l2.l2c_node import L2cNode
        return L2cNode
    elif name == "L2dNode":
        from autosmartcut.nodes.l2.l2d_node import L2dNode
        return L2dNode
    elif name == "L3Node":
        from autosmartcut.nodes.l3.l3_node import L3Node
        return L3Node
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
