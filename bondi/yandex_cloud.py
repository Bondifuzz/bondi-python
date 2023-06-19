# fmt: off
_YC_CORES = [
    2, 4, 6, 8, 10, 12, 14, 16,
    20, 24, 28, 32, 36, 40, 44,
    48, 52, 56, 60, 64, 68, 72,
    76, 80, 84, 88, 92, 96,
]

_YC_MEM_PER_CORE = [
    1, 2, 3, 4, 5, 6, 7, 8, 9,
    10, 11, 12, 13, 14, 15, 16,
]
# fmt: on

_YC_MEM_MAX = 640


def get_all_mem_values():

    res = set()
    for cores in _YC_CORES:
        for core_mem in _YC_MEM_PER_CORE:
            mem = cores * core_mem
            if mem <= _YC_MEM_MAX:
                res.add(mem)

    return list(res)


def get_all_cpu_values():
    return _YC_CORES


def is_valid_mem_per_core(cores: int, mem_gb: int):
    return mem_gb % cores == 0 and mem_gb // cores in _YC_MEM_PER_CORE
