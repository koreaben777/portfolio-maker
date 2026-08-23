from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from portfolio_maker.domain.semantic_frontier import semantic_canonical_json_bytes
from portfolio_maker.infrastructure.managed_files import (
    read_managed_relative_file,
    remove_managed_file,
    write_managed_text,
)


MAX_SEMANTIC_EXCHANGE_INPUT_BYTES = 262_144


class SemanticExchangeError(ValueError):
    pass


@dataclass(frozen=True)
class ExchangeBinding:
    run_id: str
    lease_nonce: str
    sha256: str
    byte_count: int
    input_path: Path


class SemanticExchange:
    """One replaceable, private input/output pair for one semantic v3 run."""

    def __init__(self, input_path: Path, output_path: Path) -> None:
        self._input_path = Path(input_path)
        self._output_path = Path(output_path)

    @property
    def input_path(self) -> Path:
        return self._input_path

    def publish_input(
        self,
        run_id: str,
        lease_nonce: str,
        payload: dict[str, object],
    ) -> ExchangeBinding:
        if not run_id or not lease_nonce:
            raise SemanticExchangeError("semantic exchange binding is invalid")
        content = semantic_canonical_json_bytes(payload)
        if len(content) > MAX_SEMANTIC_EXCHANGE_INPUT_BYTES:
            raise SemanticExchangeError("semantic work input exceeds byte budget")
        try:
            write_managed_text(self._input_path, content.decode("utf-8"))
            # Task 9 owns only the fixed output slot, not output acceptance.
            write_managed_text(self._output_path, "")
        except (OSError, UnicodeDecodeError) as error:
            raise SemanticExchangeError("semantic exchange is unavailable") from error
        return ExchangeBinding(
            run_id=run_id,
            lease_nonce=lease_nonce,
            sha256=sha256(content).hexdigest(),
            byte_count=len(content),
            input_path=self._input_path,
        )

    def replay_input(
        self,
        run_id: str,
        lease_nonce: str,
        expected_sha256: str,
        expected_byte_count: int,
    ) -> ExchangeBinding:
        if (
            not run_id
            or not lease_nonce
            or len(expected_sha256) != 64
            or type(expected_byte_count) is not int
            or expected_byte_count < 0
        ):
            raise SemanticExchangeError("semantic exchange binding is invalid")
        try:
            snapshot = read_managed_relative_file(
                self._input_path.parent, Path(self._input_path.name)
            )
        except OSError as error:
            raise SemanticExchangeError("semantic exchange is unavailable") from error
        if (
            snapshot.size != expected_byte_count
            or snapshot.content_hash != expected_sha256
            or snapshot.size > MAX_SEMANTIC_EXCHANGE_INPUT_BYTES
        ):
            raise SemanticExchangeError("semantic exchange binding is invalid")
        return ExchangeBinding(
            run_id=run_id,
            lease_nonce=lease_nonce,
            sha256=snapshot.content_hash,
            byte_count=snapshot.size,
            input_path=self._input_path,
        )

    def discard_slots(self) -> None:
        try:
            remove_managed_file(self._input_path, missing_ok=True)
            remove_managed_file(self._output_path, missing_ok=True)
        except OSError as error:
            raise SemanticExchangeError("semantic exchange is unavailable") from error
