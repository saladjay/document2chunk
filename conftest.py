# 让 pytest 把仓库根加入 sys.path，使顶层包 `mineru2doc` 可被 mineru2doc/tests 导入。
# （pytest 导入本 conftest 时会将其所在目录插入 sys.path[0]。）
