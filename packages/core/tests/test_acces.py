"""La fiche d'accès : ce à quoi l'agent a touché, et ce qu'on lui a refusé."""

from __future__ import annotations

from choregos_core import rapport_d_acces


def _permission(kind: str, target: str, allowed: bool = True, reason: str = "") -> dict[str, object]:
    return {
        "type": "session/request_permission",
        "payload": {"kind": kind, "target": target, "allowed": allowed, "reason": reason},
    }


def test_les_refus_passent_devant_et_gardent_leur_motif() -> None:
    """C'est ce qu'on vient chercher dans un audit. « denied » ne se lit pas ;
    « écriture interdite dans un fichier sensible » se lit."""
    rapport = rapport_d_acces(
        [
            _permission("read", "/workspace/src/panier.py"),
            _permission("read", "/workspace/src/panier.py"),
            _permission("execute", "pytest -q"),
            _permission("write", "/workspace/.env", allowed=False, reason="fichier sensible"),
        ]
    )
    assert rapport.acces[0].cible == "/workspace/.env", "le refus d'abord"
    assert rapport.acces[0].motifs == ["fichier sensible"]
    assert rapport.refuses and rapport.refuses[0].nature == "write"
    # Puis par fréquence : ce que l'agent a le plus touché se voit en premier.
    assert rapport.acces[1].cible == "/workspace/src/panier.py"
    assert rapport.acces[1].demandes == 2


def test_la_prose_de_l_agent_n_entre_pas_dans_la_fiche() -> None:
    """Le journal ACP porte surtout du texte. La fiche répond à « à quoi a-t-il touché »,
    pas à « qu'a-t-il dit » — sinon elle redevient illisible, ce qui est le problème."""
    rapport = rapport_d_acces(
        [
            {"type": "session/update", "payload": {"update": {"text": "je regarde le ticket"}}},
            _permission("read", "/workspace/README.md"),
        ]
    )
    assert [a.cible for a in rapport.acces] == ["/workspace/README.md"]
    assert rapport.evenements == 2, "on compte tout, on n'en retient qu'une partie"


def test_un_appel_d_outil_du_catalogue_est_un_acces() -> None:
    """Un agent qui interroge une API tierce touche au monde, même sans écrire un fichier."""
    rapport = rapport_d_acces(
        [{"type": "choregos.tool.called", "payload": {"tool": "verifier_adresse", "provider": "api-adresse"}}]
    )
    assert rapport.acces[0].nature == "tool"
    assert rapport.acces[0].cible == "verifier_adresse"


def test_une_nature_inconnue_reste_visible() -> None:
    """Un accès qu'on ne sait pas classer ne doit pas disparaître : c'est justement
    celui-là qu'on voudra regarder."""
    rapport = rapport_d_acces([_permission("telepathie", "quelque part")])
    assert rapport.acces[0].nature == "autre"
    assert rapport.acces[0].cible == "quelque part"
