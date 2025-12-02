import sys
import re
from pathlib import Path

INDENT_SIZE = 4


def normalize_spaces(stripped: str) -> str:
    """Podstawowe poprawki spacji w linii ST."""
    # Nie tykamy pełnolinijkowych komentarzy
    if stripped.startswith("//") or stripped.startswith("(*") or stripped.startswith("*"):
        return stripped

    s = stripped

    # Najpierw napraw ': =', ':  =', itd. -> ':='
    s = re.sub(r":\s*=\s*", ":=", s)

    # Spacja po przecinku
    s = re.sub(r",\s*", ", ", s)

    # Spacje wokół ':=' i '=' (ale '=' nie będącego częścią ':=')
    s = re.sub(r"\s*:=\s*", " := ", s)
    s = re.sub(r"(?<!:)\s*=\s*", " = ", s)

    # Nawiasy: bez spacji przed '(' i tuż po '('
    s = re.sub(r"\s+\(", "(", s)
    s = re.sub(r"\(\s+", "(", s)

    # Redukcja wielokrotnych spacji (z wyjątkiem wcięcia na początku)
    m = re.match(r"(\s*)(.*)", s)
    if m:
        leading, rest = m.groups()
    else:
        leading, rest = "", s
    rest = re.sub(r"[ \t]{2,}", " ", rest)

    return leading + rest


def reformat_if_block(lines, base_indent, indent_size=INDENT_SIZE):
    """
    lines – lista linii ST od IF ... do linii z THEN (włącznie)
    base_indent – poziom wcięcia (w "krokach" po INDENT_SIZE)
    """
    joined = " ".join(l.strip() for l in lines)
    upper = joined.upper()

    if not upper.startswith("IF ") or " THEN" not in upper:
        # Jeżeli coś nietypowego – tylko popraw spacje i wcięcie
        out = []
        for raw in lines:
            out.append(" " * (indent_size * base_indent) + normalize_spaces(raw.lstrip()))
        return out

    joined = normalize_spaces(joined).strip()

    # Wytnij środek między IF a THEN
    body = joined[len("IF "): joined.rfind(" THEN")]
    parts = re.split(r"\bOR\b", body)
    parts = [p.strip() for p in parts if p.strip()]

    out_lines = []
    indent_str = " " * (indent_size * base_indent)

    if not parts:
        # Awaryjnie – zostaw wszystko w jednej linii
        out_lines.append(indent_str + joined)
        return out_lines

    # Pierwsza linia
    out_lines.append(f"{indent_str}IF {parts[0]}")

    # Kolejne linie z OR
    if len(parts) > 1:
        for cond in parts[1:]:
            out_lines.append(f"{indent_str}OR {cond} THEN")
        # Ostatnia linia ma już THEN – poprawimy, żeby było tylko raz
        # (poniższa pętla da "OR X THEN" tylko w ostatniej linijce)
        if len(parts) > 2:
            # Wszystkie oprócz ostatniej: bez THEN
            out_lines = [out_lines[0]] + [
                f"{indent_str}OR {c}" for c in parts[1:-1]
            ] + [f"{indent_str}OR {parts[-1]} THEN"]
    else:
        out_lines[-1] = f"{indent_str}IF {parts[0]} THEN"

    return out_lines


def format_st_block(st: str, indent_size: int = INDENT_SIZE) -> str:
    """Formatowanie ST: łamanie IF z OR, wcięcia, spacing, puste linie."""
    raw_lines = st.splitlines()
    result = []
    indent = 0
    blank_count = 0

    DEDENT_TOKENS = ("END_IF", "END_CASE", "END_FOR", "END_WHILE", "UNTIL")
    DEDENT_BEFORE = DEDENT_TOKENS + ("ELSE", "ELSIF")

    i = 0
    n = len(raw_lines)

    while i < n:
        raw = raw_lines[i]
        line = raw.rstrip()
        stripped = line.lstrip()

        # Puste linie – max 1 pod rząd (żeby nie puchło po THEN)
        if stripped == "":
            blank_count += 1
            if blank_count <= 1:
                result.append("")
            i += 1
            continue
        else:
            blank_count = 0

        upper = stripped.upper()

        # Komentarze blokowe
        if stripped.startswith("(*") or stripped.startswith("*)"):
            result.append(" " * (indent_size * indent) + stripped)
            i += 1
            continue

        # Dedent przed END_*, ELSE, ELSIF
        if any(upper.startswith(tok) for tok in DEDENT_BEFORE):
            indent = max(indent - 1, 0)

        # IF ... THEN – zbierz cały blok i przeformatuj
        if upper.startswith("IF "):
            if_block_lines = [stripped]
            j = i + 1
            found_then = "THEN" in upper
            while j < n and not found_then:
                next_line = raw_lines[j].rstrip()
                if "THEN" in next_line.upper():
                    found_then = True
                if_block_lines.append(next_line.lstrip())
                j += 1

            reformatted = reformat_if_block(if_block_lines, indent, indent_size)
            result.extend(reformatted)
            indent += 1  # po THEN wchodzimy głębiej
            i = j
            continue

        # Zwykła linia – normalizacja spacji + wcięcie
        normalized = normalize_spaces(stripped)
        res_line = " " * (indent_size * indent) + normalized
        result.append(res_line)

        upper_norm = normalized.upper()
        if "THEN" in upper_norm:
            indent += 1
        elif upper_norm.startswith("ELSE"):
            indent += 1
        elif upper_norm.startswith("CASE") or upper_norm.startswith("FOR") \
                or upper_norm.startswith("WHILE") or upper_norm.startswith("REPEAT"):
            indent += 1

        i += 1

    return "\n".join(result)


def format_case_blocks(st: str, indent_size: int = INDENT_SIZE) -> str:
    """
    Druga faza: porządkowanie CASE:
    - etykiety na jednym poziomie
    - instrukcje (i ';') o 1 poziom głębiej
    - jeśli po etykiecie jest kod + osobny ';' -> ';' usuwamy
    - jeśli jest tylko ';' (puste ramię) -> zostawiamy jedno
    """
    lines = st.splitlines()
    result = []
    inside_case = False
    case_base_indent = None
    label_indent = None
    last_label_has_code = False
    last_label_index = None

    def count_leading_spaces(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        upper = stripped.upper()

        # CASE ... OF
        if upper.startswith("CASE ") and upper.endswith(" OF"):
            inside_case = True
            case_base_indent = count_leading_spaces(line)
            label_indent = None
            last_label_has_code = False
            last_label_index = None
            result.append(line)
            continue

        if inside_case:
            # KONIEC CASE
            if upper.startswith("END_CASE"):
                inside_case = False
                case_base_indent = None
                label_indent = None
                last_label_has_code = False
                last_label_index = None
                result.append(line)
                continue

            # Pusta linia / komentarz
            if stripped == "" or stripped.startswith("//") or stripped.startswith("(*"):
                result.append(line)
                continue

            # Etykieta: xxx:
            if stripped.endswith(":"):
                if label_indent is None:
                    label_indent = count_leading_spaces(line)
                indent_spaces = " " * label_indent
                result.append(indent_spaces + stripped)
                last_label_has_code = False
                last_label_index = len(result) - 1
                continue

            # Samotne ';'
            if stripped == ";":
                if last_label_index is not None:
                    if last_label_has_code:
                        # Był już kod po tej etykiecie -> ';' jest śmieciem, usuwamy
                        continue
                    else:
                        # Puste ramię – zostaw placeholder ';' we wcięciu głębiej
                        effective_label_indent = label_indent if label_indent is not None else (case_base_indent or 0)
                        stmt_indent = effective_label_indent + indent_size
                        result.append(" " * stmt_indent + ";")
                        continue
                else:
                    # ';' bez etykiety – zostaw jak jest
                    result.append(line)
                    continue

            # Normalna linia kodu w CASE: wcięta 1 poziom głębiej niż etykieta
            effective_label_indent = label_indent if label_indent is not None else (case_base_indent or 0)
            stmt_indent = effective_label_indent + indent_size
            result.append(" " * stmt_indent + stripped)
            last_label_has_code = True
            continue

        # Poza CASE – przepisujemy bez zmian
        result.append(line)

    return "\n".join(result)


def format_tc_pou_file(path: Path) -> bool:
    """Sformatuj ST wewnątrz <ST><![CDATA[ ... ]]></ST> w jednym pliku .TcPOU."""
    text = path.read_text(encoding="utf-8")

    pattern = re.compile(r"(<ST><!\[CDATA\[)(.*?)(\]\]></ST>)", re.DOTALL)
    changed = False

    def repl(m: re.Match) -> str:
        nonlocal changed
        prefix, code, suffix = m.groups()
        st_formatted = format_st_block(code)
        st_formatted = format_case_blocks(st_formatted)
        if st_formatted != code:
            changed = True
        return prefix + st_formatted + suffix

    new_text, count = pattern.subn(repl, text)

    if changed:
        path.write_text(new_text, encoding="utf-8")
        print(f"Formatted {path} (ST blocks: {count})")
        return True
    else:
        print(f"No changes in {path} (ST blocks: {count})")
        return False


def main():
    if len(sys.argv) < 2:
        print("Użycie:")
        print("  python format_tc_st.py plik.TcPOU")
        print("  python format_tc_st.py katalog")
        sys.exit(1)

    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            for file in p.rglob("*.TcPOU"):
                format_tc_pou_file(file)
        elif p.is_file() and p.suffix.lower() == ".tcpou":
            format_tc_pou_file(p)
        else:
            print(f"Pominięto {arg} (nie katalog i nie .TcPOU)")


if __name__ == "__main__":
    main()
