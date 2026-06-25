from uuid import NAMESPACE_URL, uuid5


class VectorStoreError(Exception):
    pass


class VectorStoreUnavailable(VectorStoreError):
    pass


def stable_vector_id(source_type: str, source_id: str, chunk_index: int) -> str:
    name = f"auto-reign:{source_type}:{source_id}:{chunk_index}"
    return str(uuid5(NAMESPACE_URL, name))
