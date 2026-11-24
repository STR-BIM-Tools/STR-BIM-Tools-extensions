# -*- coding: utf-8 -*-
# =============================================================================
# fgh_percement.py - Percements crees par « Percer Reservation » : a qui ils
# appartiennent, comment les retirer ou les deplacer avec leur reservation.
#
# Un percement, ce sont trois sortes d'objets distincts de la reservation :
#   - une famille de coupe (vides) generee, une par reservation ;
#   - son occurrence, qui decoupe les elements traverses ;
#   - des bouchons de phase (DirectShape), un par element traverse.
#
# CLE DE RATTACHEMENT
# -------------------
# Jusqu'a « Percer Reservation » 1.0.0, ces objets etaient reperes par le N° de
# reservation (STR_PERCEMENT_<N°>, STR_BOUCHON|<N°>|...). Le N° n'est pas une
# identite : il existe en double (527 dans la RES au 24.11.2026) et « Sync
# Reservations » le met a jour en place (prefixe demandeur corrige). Percer une
# reservation retirait alors le percement d'une autre, ou ne retrouvait plus le
# sien. Depuis la 1.1.0 la cle est l'ElementId de la reservation dans la STR :
#   famille  STR_PERCEMENT_ID<ElementId>
#   bouchon  STR_BOUCHON|ID<ElementId>|<ElementId de l'hote>
#
# REPRISE DES ANCIENS PERCEMENTS
# ------------------------------
# Un percement a l'ancienne cle est rattache a une reservation si son occurrence
# de coupe se trouve dans la boite englobante de la reservation (elle est posee
# au centre de ses solides), ou si la reservation est la SEULE de la maquette a
# porter ce N°. Sinon il n'est pas touche : jamais devine.
#
# Les fonctions qui ecrivent s'appellent dans une transaction deja ouverte.
# Compatible IronPython 2.7 (Revit 2024) et 3.4 (Revit 2025+).
# =============================================================================

import re

from Autodesk.Revit.DB import (FilteredElementCollector, FamilyInstance, Family,
                               DirectShape, InstanceVoidCutUtils,
                               ElementTransformUtils, ElementId)

import fgh_res

CUTTER_FAM_PREFIX = u"STR_PERCEMENT_"
TAG_CUTTER = u"STR_PERCEMENT"
TAG_BOUCHON = u"STR_BOUCHON"
CLE_PREFIXE = u"ID"

_RX_NOUVELLE_CLE = re.compile(u"^" + CUTTER_FAM_PREFIX + CLE_PREFIXE + u"(\\d+)$")
_BAD_CHARS = u"\\:{}[]|;<>?`~"
_MARGE_BOITE_FT = 0.01 / 0.3048      # 1 cm


def sanitize(nom):
    """Retire les caracteres refuses par Revit dans les noms d'elements."""
    out = []
    for ch in (nom or u""):
        out.append(u"_" if ch in _BAD_CHARS or ch == u"/" else ch)
    return u"".join(out).strip() or u"SANS_NUM"


def cle(res):
    """Cle de rattachement d'une reservation : ID<ElementId>."""
    return u"{}{}".format(CLE_PREFIXE, fgh_res.id_int(res.Id))


def nom_famille(res):
    return CUTTER_FAM_PREFIX + cle(res)


def prefixe_bouchon(res):
    return u"{}|{}|".format(TAG_BOUCHON, cle(res))


def numero(res):
    return (fgh_res.texte_param(res, fgh_res.PARAM_NUM) or u"").strip()


# =============================================================================
# Recherche
# =============================================================================
def _nom(el):
    return fgh_res.nom(el)


def _familles_coupe(doc):
    """{nom: Family} des familles de coupe generees par l'outil."""
    out = {}
    for f in FilteredElementCollector(doc).OfClass(Family):
        n = _nom(f)
        if n.startswith(CUTTER_FAM_PREFIX):
            out[n] = f
    return out


def _occurrences(doc, familles):
    """{nom de famille: [FamilyInstance]} pour les familles donnees."""
    par_type = {}
    for n, f in familles.items():
        try:
            for sid in f.GetFamilySymbolIds():
                par_type[fgh_res.id_int(sid)] = n
        except Exception:
            pass
    out = dict((n, []) for n in familles)
    if not par_type:
        return out
    for fi in FilteredElementCollector(doc).OfClass(FamilyInstance).WhereElementIsNotElementType():
        try:
            n = par_type.get(fgh_res.id_int(fi.GetTypeId()))
            if n is not None:
                out[n].append(fi)
        except Exception:
            pass
    return out


def _dans_boite(res, point):
    try:
        bb = res.get_BoundingBox(None)
        if bb is None or point is None:
            return False
        m = _MARGE_BOITE_FT
        return (bb.Min.X - m <= point.X <= bb.Max.X + m and
                bb.Min.Y - m <= point.Y <= bb.Max.Y + m and
                bb.Min.Z - m <= point.Z <= bb.Max.Z + m)
    except Exception:
        return False


def _point(inst):
    try:
        return inst.Location.Point
    except Exception:
        return None


class Index(object):
    """Lecture unique de la maquette, pour interroger plusieurs reservations."""
    def __init__(self, doc):
        self.doc = doc
        self.familles = _familles_coupe(doc)
        self.occurrences = _occurrences(doc, self.familles)
        self.bouchons = []
        for ds in FilteredElementCollector(doc).OfClass(DirectShape).WhereElementIsNotElementType():
            try:
                tag = ds.ApplicationDataId or u""
                if tag.startswith(TAG_BOUCHON + u"|"):
                    self.bouchons.append((tag, ds))
            except Exception:
                pass
        self._nb_par_num = None

    def nb_reservations(self, num):
        """Nombre de reservations FGH_RES de la maquette portant ce N°."""
        if self._nb_par_num is None:
            self._nb_par_num = {}
            for e in fgh_res.reservations(self.doc, avec_supprimees=True):
                n = numero(e)
                if n:
                    self._nb_par_num[n] = self._nb_par_num.get(n, 0) + 1
        return self._nb_par_num.get(num, 0)

    def _anciennes(self, res):
        """Noms des familles a l'ancienne cle (N°) rattachables a la reservation."""
        num = numero(res)
        nom_num = CUTTER_FAM_PREFIX + sanitize(num) if num else None
        out = []
        for n in self.familles:
            if _RX_NOUVELLE_CLE.match(n):
                continue
            occs = self.occurrences.get(n, [])
            if any(_dans_boite(res, _point(i)) for i in occs):
                out.append(n)
            elif nom_num is not None and n == nom_num and self.nb_reservations(num) == 1:
                out.append(n)
        return out

    def trouver(self, res):
        """
        Objets du percement d'une reservation :
        {"familles": [Family], "occurrences": [FamilyInstance],
         "bouchons": [DirectShape], "ancienne_cle": bool}.
        """
        noms = []
        n_new = nom_famille(res)
        if n_new in self.familles:
            noms.append(n_new)
        anciennes = self._anciennes(res)
        noms += anciennes

        prefixes = [prefixe_bouchon(res)]
        for n in anciennes:
            prefixes.append(u"{}|{}|".format(TAG_BOUCHON, n[len(CUTTER_FAM_PREFIX):]))
        bouchons = [ds for tag, ds in self.bouchons
                    if any(tag.startswith(p) for p in prefixes)]
        occs = []
        for n in noms:
            occs += self.occurrences.get(n, [])
        return {"familles": [self.familles[n] for n in noms], "occurrences": occs,
                "bouchons": bouchons, "ancienne_cle": bool(anciennes)}

    def a_un_percement(self, res):
        t = self.trouver(res)
        return bool(t["occurrences"] or t["bouchons"])

    def orphelins(self):
        """
        Percements a la nouvelle cle dont la reservation n'existe plus dans la
        maquette (supprimee a la main, par exemple). Retourne une liste de
        {"cle", "familles", "occurrences", "bouchons"}. Les percements a
        l'ancienne cle ne sont jamais declares orphelins : leur reservation ne
        peut pas etre identifiee avec certitude.
        """
        par_cle = {}
        for n, f in self.familles.items():
            m = _RX_NOUVELLE_CLE.match(n)
            if m:
                d = par_cle.setdefault(m.group(1), {"familles": [], "occurrences": [], "bouchons": []})
                d["familles"].append(f)
                d["occurrences"] += self.occurrences.get(n, [])
        rx_b = re.compile(u"^" + TAG_BOUCHON + u"\\|" + CLE_PREFIXE + u"(\\d+)\\|")
        for tag, ds in self.bouchons:
            m = rx_b.match(tag)
            if m:
                d = par_cle.setdefault(m.group(1), {"familles": [], "occurrences": [], "bouchons": []})
                d["bouchons"].append(ds)
        out = []
        for k in sorted(par_cle):
            try:
                el = self.doc.GetElement(ElementId(int(k)))
            except Exception:
                el = None
            # Orphelin seulement si l'element n'existe plus : « Percer Reservation »
            # accepte, apres confirmation, un element d'une autre famille.
            if el is None:
                d = par_cle[k]
                d["cle"] = CLE_PREFIXE + k
                out.append(d)
        return out


# =============================================================================
# Ecriture (transaction ouverte par l'appelant)
# =============================================================================
def _supprimer(doc, el):
    try:
        if el.Pinned:
            el.Pinned = False
    except Exception:
        pass
    try:
        doc.Delete(el.Id)
        return True
    except Exception:
        return False


def retirer_objets(doc, objets):
    """
    Retire les coupes, les occurrences de coupe, les bouchons et les familles
    de coupe d'un resultat de Index.trouver() ou d'un orphelin.
    Retourne (hotes_liberes, nb_bouchons).
    """
    hotes = []
    for inst in objets.get("occurrences", []):
        try:
            for hid in InstanceVoidCutUtils.GetElementsBeingCut(inst):
                hote = doc.GetElement(hid)
                if hote is None:
                    continue
                try:
                    InstanceVoidCutUtils.RemoveInstanceVoidCut(doc, hote, inst)
                    hotes.append(hote)
                except Exception:
                    pass
        except Exception:
            pass
        _supprimer(doc, inst)
    nb_b = 0
    for ds in objets.get("bouchons", []):
        if _supprimer(doc, ds):
            nb_b += 1
    for fam in objets.get("familles", []):
        try:
            doc.Delete(fam.Id)
        except Exception:
            pass
    return hotes, nb_b


def retirer(doc, res, index=None):
    """Retire le percement d'une reservation. Retourne (hotes_liberes, nb_bouchons)."""
    index = index or Index(doc)
    return retirer_objets(doc, index.trouver(res))


def deplacer(doc, res, vecteur, index=None):
    """
    Deplace le percement d'une reservation du meme vecteur qu'elle : la coupe
    suit son occurrence. Les bouchons sont translates tels quels ; si la
    reservation change d'element traverse, « Percer Reservation » est a relancer.
    Retourne (nb_occurrences_deplacees, nb_bouchons_deplaces).
    """
    index = index or Index(doc)
    t = index.trouver(res)
    nb = [0, 0]
    for rang, cle_liste in enumerate(("occurrences", "bouchons")):
        for el in t[cle_liste]:
            epingle = False
            try:
                epingle = el.Pinned
                if epingle:
                    el.Pinned = False
                ElementTransformUtils.MoveElement(doc, el.Id, vecteur)
                nb[rang] += 1
            except Exception:
                pass
            if epingle:
                try:
                    el.Pinned = True
                except Exception:
                    pass
    return nb[0], nb[1]
