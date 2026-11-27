"""Merge a Reveal batch's bottom-to-top PNG sequence into the analysis stack."""


def apply_batch_order(layers, returned_ids):
    """Only replace positions owned by objects mapped in this batch."""
    by_id = {layer["id"]: layer for layer in layers}
    members = set(returned_ids)
    positions = [i for i, layer in enumerate(layers) if layer["id"] in members]
    for i, oid in zip(positions, returned_ids, strict=True):
        layers[i] = by_id[oid]
