"""Explicit DGT changes and clarifications of earlier consultas."""

from dgt_consultas.criteria import links_in


def _body(answer: str, question: str = "Tributación.") -> str:
    return (
        "# Consulta Vinculante V0500-22\n\n## Descripcion de hechos\n\nHechos.\n\n"
        f"## Cuestion planteada\n\n{question}\n\n## Contestacion\n\n{answer}\n"
    )


def links(answer: str, question: str = "Tributación.") -> list[tuple[str, str]]:
    return [
        (l.kind, l.target)
        for l in links_in("V0500-22", "2022-03-14", _body(answer, question))
    ]


def test_change_of_criterion_formula():
    assert links(
        "Esta conclusión supone un cambio de criterio respecto al mantenido por esta Dirección "
        "General, entre otras, en la contestación vinculante de 7 de abril de 2011, número V0939-11."
    ) == [("changes", "V0939-11")]
    assert links(
        "Por lo tanto, este Centro directivo modifica su criterio recogido en la consulta de fecha "
        "23 de abril de 2012, número V0861-12, y dispone que los cursos están exentos."
    ) == [("changes", "V0861-12")]


def test_consulta_adopting_the_new_criterion_is_not_superseded():
    # The number before the formula is where the new criterion was adopted.
    assert links(
        "Esta unificación de criterio fijada por el TEAC ha sido asumida por este Centro directivo en "
        "su contestación vinculante nº V2761-20, lo que supone un cambio de criterio respecto del "
        "mantenido por esta Dirección General en la consulta V1234-19."
    ) == [("changes", "V1234-19")]
    # A coordinated clause after the list names the new one.
    assert links(
        "Este criterio supone un cambio de criterio respecto a la reiterada doctrina de este Centro "
        "directivo recogida, entre otras, en la contestación vinculante número V0257-21, y así ha "
        "sido puesto de manifiesto por el mismo en la contestación vinculante número V2554-23."
    ) == [("changes", "V0257-21")]


def test_other_bodies_changes_are_ignored():
    assert (
        links(
            "El Tribunal Supremo supone un cambio de criterio respecto de su doctrina anterior, "
            "recogida en la consulta V0001-19."
        )
        == []
    )


def test_clarification_request():
    assert links(
        "Respuesta.",
        "Se solicita aclaración de la consulta V0609-15, de 18 de febrero.",
    ) == [("clarifies", "V0609-15")]


def _links(text):
    return [
        (l.kind, l.target)
        for l in links_in("V0001-25", "2025-01-01", f"## Contestacion\n\n{text}\n")
    ]


def test_dgt_vocabulary_counts_as_own_doctrine():
    assert _links(
        "Esta conclusión supone un cambio de criterio respecto de lo manifestado por la "
        "Dirección General de Tributos en la consulta vinculante V1081-12."
    ) == [("changes", "V1081-12")]


def test_abbreviation_before_number_does_not_split_sentence():
    assert _links(
        "Esta conclusión supone un cambio de criterio respecto al mantenido por esta "
        "Dirección General en la contestación de 4 de abril de 2011, núm. V0939-11."
    ) == [("changes", "V0939-11")]


def test_negated_change_is_not_a_change():
    assert (
        _links(
            "Esta conclusión no supone un cambio de criterio respecto al mantenido por esta "
            "Dirección General en la consulta V0939-11."
        )
        == []
    )


def test_lowercase_number():
    assert _links(
        "Esta Dirección General modifica el criterio que venía manteniendo en la consulta v1649-19."
    ) == [("changes", "V1649-19")]
