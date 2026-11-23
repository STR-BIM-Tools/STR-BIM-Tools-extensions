# -*- coding: utf-8 -*-
# =============================================================================
# CONFIG
# =============================================================================
SCRIPT_VERSION = "1.0.0"
SCRIPT_AUTHOR  = "Tony VARIN"
TOOL_NAME      = "FGH - Doublons ADN"
# =============================================================================
# Audit des ADN (CONTROL_NE_PAS_TOUCHER) en double dans la maquette RES.
#
# L'ADN est la cle d'appariement de « Sync Reservations » : la synchro range
# les reservations dans un dictionnaire par ADN, si bien qu'entre deux objets
# au meme ADN seul le dernier lu est compare, l'autre disparait sans bruit.
# Cet outil montre ces groupes et dit, pour chacun :
#   - Copie superposee : meme position (<= 1 cm), meme famille, type et
#     encombrement -> doublon pur, un exemplaire est en trop dans la RES ;
#   - Meme position, geometrie differente : type ou encombrement different ;
#   - Positions differentes : reservations distinctes qui partagent un ADN,
#     la synchro en perd au moins une ;
#   - les parametres qui different entre jumeaux (N°, statut VAL, supprime...).
#
# Lancement :
#   - dans la maquette RES (FGH_RES_SPV...) : analyse de la maquette ouverte ;
#   - dans la STR : analyse du lien FGH_RES_SPV et, en plus, des copies STR
#     (la synchro indexe aussi la STR par ADN).
# Collecte identique a Sync Reservations (CLAUDE.md) :
#   RES : famille FGH_RES* + ADN contenant _STATUT, hors FGH_MUTUALISE = 1 ;
#   STR : famille FGH_RES*, hors PVA_RES_VAL_Statut = SUPPRIMEE.
# Lecture seule : l'outil n'ecrit rien dans la maquette.
# =============================================================================

import clr, traceback
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons


def _show_fatal(title, ex):
    td = TaskDialog(title)
    td.MainContent     = u"Erreur : " + u"{}".format(ex)
    td.ExpandedContent = traceback.format_exc()
    td.CommonButtons   = TaskDialogCommonButtons.Close
    td.Show()


try:
    import io, os, json, re
    clr.AddReference("PresentationFramework"); clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase"); clr.AddReference("System")
    import System
    import System.Windows as SW
    from System.Windows import (Window, Thickness, MessageBox, MessageBoxButton,
                                MessageBoxImage, WindowStartupLocation, SystemColors)
    from System.Windows.Controls import (Grid, StackPanel, DataGrid, DataGridRow, TextBlock,
                                         Border, DataGridTextColumn, Orientation,
                                         DataGridLength, DataGridLengthUnitType,
                                         DataGridHeadersVisibility, DataGridGridLinesVisibility,
                                         DataGridSelectionMode, DataGridSelectionUnit)
    from System.Windows.Data import Binding
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import (FilteredElementCollector, FamilyInstance, RevitLinkInstance,
                                   StorageType, XYZ, ElementId, Reference)
    import str_ui
    import fgh_res

    doc   = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    app   = __revit__.Application

    # =========================================================================
    # CONSTANTES METIER — alignees sur Sync Reservations
    # =========================================================================
    LINK_MODEL_PREFIX = fgh_res.MAQUETTE_RES_PREFIXE      # "FGH_RES_SPV"
    FAMILY_PREFIX     = fgh_res.FAMILY_PREFIX             # "FGH_RES"
    ADN_PARAM         = fgh_res.ADN_PARAM                 # "CONTROL_NE_PAS_TOUCHER"
    ADN_FILTER_VAL    = u"_STATUT"
    MUTUALISE_PARAM   = u"FGH_MUTUALISE"

    PARAM_NUM         = fgh_res.PARAM_NUM
    PARAM_DEMANDEUR   = u"PVA_RES_DEM_Demandeur"
    PARAM_DATE        = u"PVA_RES_DEM_Date"
    PARAM_NIVEAU      = u"WSP_NiveauAssocie"
    PARAM_VAL_STATUT  = fgh_res.P_STATUT
    PARAM_SUPPRIME    = fgh_res.SUPPRIME_PARAM
    PARAM_X           = u"WSP_PositionX_NM95"
    PARAM_Y           = u"WSP_PositionY_NM95"
    PARAM_Z           = u"WSP_PositionZ_NM95"
    STATUT_SUPPRIMEE  = u"SUPPRIMEE"

    # Parametres compares entre jumeaux (nom -> libelle court)
    PARAMS_JUMEAUX = [
        (PARAM_NUM, u"N°"),
        (PARAM_VAL_STATUT, u"Statut VAL"),
        (fgh_res.P_DATE, u"Date VAL"),
        (PARAM_SUPPRIME, u"Supprimé"),
        (u"PVA_RES_DEM_Modifie", u"Modifié"),
        (PARAM_DEMANDEUR, u"Demandeur"),
        (PARAM_NIVEAU, u"Niveau"),
    ]

    SRC_RES = u"RES"
    SRC_STR = u"STR"

    TOL_POSITION_CM = 1.0
    TOL_DIM_CM      = 1.0

    V_COPIE  = u"Copie superposée"
    V_GEOM   = u"Même position, géométrie différente"
    V_DIFF   = u"Positions différentes"
    V_INCONN = u"Position indéterminée"
    GRAVITE  = {V_DIFF: 0, V_INCONN: 1, V_GEOM: 2, V_COPIE: 3}

    F_TOUT, F_DIFF, F_GEOM, F_COPIE, F_PARAM, F_RES, F_STR = (
        u"Tout afficher", V_DIFF, V_GEOM, V_COPIE, u"Paramètres divergents",
        u"Maquette RES seulement", u"Maquette STR seulement")

    BG_DIFF  = "#FDDEDE"
    BG_GEOM  = "#FFF3CD"
    BG_GRP_A = "#FFFFFF"
    BG_GRP_B = "#E8F0FA"

    _PREFS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prefs.json")
    _RX_REV = re.compile(r"_R\d+$")

    # =========================================================================
    # HELPERS
    # =========================================================================
    def _p_str(el, pname):
        return (fgh_res.texte_param(el, pname) or u"").strip()

    def _p_int(el, pname):
        try:
            p = el.LookupParameter(pname)
            if p is not None and p.HasValue and p.StorageType == StorageType.Integer:
                return p.AsInteger()
        except Exception:
            pass
        return None

    def _type_name(el):
        try:
            return fgh_res.nom(el.Document.GetElement(el.GetTypeId()))
        except Exception:
            return u""

    def _cm(ft):
        return float(ft) * 30.48

    def _point_of(el):
        """Point d'insertion, a defaut centre de la boite englobante."""
        try:
            p = getattr(el.Location, "Point", None)
            if p is not None:
                return p
        except Exception:
            pass
        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                return bb.Min.Add(bb.Max).Multiply(0.5)
        except Exception:
            pass
        return None

    def _dims_cm(el):
        """Encombrement (dx, dy, dz) de la boite englobante, en cm."""
        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                return (_cm(bb.Max.X - bb.Min.X), _cm(bb.Max.Y - bb.Min.Y),
                        _cm(bb.Max.Z - bb.Min.Z))
        except Exception:
            pass
        return None

    def _dist_cm(p1, p2):
        if p1 is None or p2 is None:
            return None
        try:
            return _cm(p1.DistanceTo(p2))
        except Exception:
            return None

    def _dims_egales(a, b):
        if a is None or b is None:
            return a is None and b is None
        return all(abs(a[i] - b[i]) <= TOL_DIM_CM for i in range(3))

    def parse_adn(adn):
        """<n°>_<date>_<heure>_STATUT_<n>_<maquette>_R<rev> -> dict, ou None."""
        if not adn:
            return None
        parts = adn.split(u"_")
        try:
            i = parts.index(u"STATUT")
        except ValueError:
            return None
        if i < 2 or len(parts) < i + 3:
            return None
        maq = u"_".join(parts[i + 2:])
        return {"num": parts[0], "maquette": maq, "maq_sr": _RX_REV.sub(u"", maq)}

    def _load_prefs():
        try:
            with io.open(_PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_prefs(d):
        try:
            txt = json.dumps(d, ensure_ascii=False)
            if not isinstance(txt, type(u"")):
                txt = txt.decode("utf-8")
            with io.open(_PREFS_FILE, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception:
            pass

    # =========================================================================
    # COLLECTE — memes filtres et meme ordre de lecture que Sync Reservations
    # =========================================================================
    def find_link_instance():
        for lnk in FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements():
            try:
                if LINK_MODEL_PREFIX in lnk.Name:
                    return lnk
            except Exception:
                pass
        return None

    def collect_res(rdoc, stats):
        """FGH_RES* + ADN contenant _STATUT, hors FGH_MUTUALISE = 1."""
        out = []
        for e in FilteredElementCollector(rdoc).OfClass(FamilyInstance).ToElements():
            try:
                if not fgh_res.famille(e).startswith(FAMILY_PREFIX):
                    continue
                stats["familles"] += 1
                adn = _p_str(e, ADN_PARAM)
                if not adn:
                    stats["sans_adn"] += 1
                    continue
                if ADN_FILTER_VAL not in adn:
                    stats["sans_statut"] += 1
                    continue
                if _p_int(e, MUTUALISE_PARAM) == 1:
                    stats["mutualisees"] += 1
                    continue
                out.append(e)
            except Exception:
                pass
        return out

    def collect_str(stats):
        """FGH_RES* de la STR, hors PVA_RES_VAL_Statut = SUPPRIMEE."""
        out = []
        for e in FilteredElementCollector(doc).OfClass(FamilyInstance).ToElements():
            try:
                if not fgh_res.famille(e).startswith(FAMILY_PREFIX):
                    continue
                if _p_str(e, PARAM_VAL_STATUT) == STATUT_SUPPRIMEE:
                    continue
                stats["familles"] += 1
                if not _p_str(e, ADN_PARAM):
                    stats["sans_adn"] += 1
                    continue
                out.append(e)
            except Exception:
                pass
        return out

    def _stats_vides():
        return {"familles": 0, "sans_adn": 0, "sans_statut": 0, "mutualisees": 0,
                "analysees": 0, "adn_dbl": 0, "objets": 0, "en_trop": 0,
                "diff": 0, "geom": 0, "copie": 0, "param": 0}

    # =========================================================================
    # ANALYSE
    # =========================================================================
    class Row(object):
        """Une occurrence d'un ADN en doublon."""
        def __init__(self, src, el, adn, grp, rang, nb, ecart, verdict, ecarts_param,
                     retenue, bg, link):
            self.Source   = src
            self.Adn      = adn
            self.Groupe   = grp
            self.Occ      = u"{}/{}".format(rang + 1, nb)
            self.Num      = _p_str(el, PARAM_NUM)
            self.Famille  = fgh_res.famille(el)
            self.Type     = _type_name(el)
            self.Niveau   = _p_str(el, PARAM_NIVEAU)
            self.Statut   = _p_str(el, PARAM_VAL_STATUT)
            self.Supprime = u"Oui" if _p_int(el, PARAM_SUPPRIME) == 1 else u""
            self.Dem      = _p_str(el, PARAM_DEMANDEUR)
            self.DateDem  = _p_str(el, PARAM_DATE)
            self.Id       = u"{}".format(fgh_res.id_int(el.Id))
            self.UniqueId = el.UniqueId
            self.Verdict  = verdict
            self.Ecarts   = ecarts_param
            self.Synchro  = u"Retenue" if retenue else u"Ignorée"
            self.Ecart    = u"réf." if rang == 0 else (u"?" if ecart is None
                                                         else u"{:.1f}".format(ecart))
            info = parse_adn(adn)
            self.Parse    = u"ok" if info is not None else u"ADN non parsable"
            self.NumAdn   = info["num"] if info else u""
            self.MaqSR    = info["maq_sr"] if info else u""
            d = _dims_cm(el)
            self.Dims     = u"{:.0f} x {:.0f} x {:.0f}".format(*d) if d else u""
            p = _point_of(el)
            self.Xm = u"{:.3f}".format(p.X * 0.3048) if p is not None else u""
            self.Ym = u"{:.3f}".format(p.Y * 0.3048) if p is not None else u""
            self.Zm = u"{:.3f}".format(p.Z * 0.3048) if p is not None else u""
            self.Xn = _p_str(el, PARAM_X)
            self.Yn = _p_str(el, PARAM_Y)
            self.Zn = _p_str(el, PARAM_Z)
            self.RowBg = bg
            self.Tri  = (GRAVITE.get(verdict, 9), src, grp, rang)
            self._el   = el
            self._link = link

    def analyser(elements, src, stats, link, grp_depart):
        """Groupes d'ADN portes par plusieurs objets. Retourne (rows, prochain n° groupe)."""
        stats["analysees"] = len(elements)
        par_adn, ordre = {}, []
        for e in elements:
            k = _p_str(e, ADN_PARAM)
            if k not in par_adn:
                par_adn[k] = []
                ordre.append(k)
            par_adn[k].append(e)

        rows, grp = [], grp_depart
        for adn in ordre:
            groupe = par_adn[adn]
            if len(groupe) < 2:
                continue
            grp += 1
            stats["adn_dbl"] += 1
            stats["objets"] += len(groupe)
            stats["en_trop"] += len(groupe) - 1

            ref = groupe[0]
            ref_pt, ref_dims = _point_of(ref), _dims_cm(ref)
            ref_ft = (fgh_res.famille(ref), _type_name(ref))
            ecarts, emax, inconnu, geom_diff = [None], 0.0, False, False
            for e in groupe[1:]:
                d = _dist_cm(ref_pt, _point_of(e))
                ecarts.append(d)
                if d is None:
                    inconnu = True
                elif d > emax:
                    emax = d
                if (fgh_res.famille(e), _type_name(e)) != ref_ft or \
                        not _dims_egales(ref_dims, _dims_cm(e)):
                    geom_diff = True

            if inconnu:
                verdict = V_INCONN
            elif emax > TOL_POSITION_CM:
                verdict, bg = V_DIFF, BG_DIFF
                stats["diff"] += 1
            elif geom_diff:
                verdict = V_GEOM
                stats["geom"] += 1
            else:
                verdict = V_COPIE
                stats["copie"] += 1
            if verdict == V_INCONN or verdict == V_GEOM:
                bg = BG_GEOM
            elif verdict == V_COPIE:
                bg = BG_GRP_A if grp % 2 else BG_GRP_B

            divergents = []
            for pname, lib in PARAMS_JUMEAUX:
                if len(set(_p_str(e, pname) for e in groupe)) > 1:
                    divergents.append(lib)
            if divergents:
                stats["param"] += 1
            txt_div = u", ".join(divergents)

            # La synchro range par ADN dans un dict : le dernier lu ecrase les autres.
            for i, e in enumerate(groupe):
                rows.append(Row(src, e, adn, grp, i, len(groupe), ecarts[i], verdict,
                                txt_div, i == len(groupe) - 1, bg, link))
        return rows, grp

    # =========================================================================
    # EXPORT EXCEL
    # =========================================================================
    COLS = [
        (u"Source", "Source", 9), (u"Groupe", "Groupe", 8), (u"Occurrence", "Occ", 11),
        (u"ADN", "Adn", 64), (u"Verdict groupe", "Verdict", 30),
        (u"Paramètres divergents", "Ecarts", 28), (u"Synchro", "Synchro", 10),
        (u"Écart (cm)", "Ecart", 11), (u"Encombrement (cm)", "Dims", 18),
        (u"N° réservation", "Num", 16), (u"N° (ADN)", "NumAdn", 14),
        (u"Maquette d'origine", "MaqSR", 32), (u"Lecture ADN", "Parse", 16),
        (u"Famille", "Famille", 26), (u"Type", "Type", 22), (u"Niveau", "Niveau", 14),
        (u"Statut VAL", "Statut", 14), (u"Supprimé", "Supprime", 10),
        (u"Demandeur", "Dem", 16), (u"Date demande", "DateDem", 13),
        (u"X projet (m)", "Xm", 12), (u"Y projet (m)", "Ym", 12), (u"Z projet (m)", "Zm", 12),
        (u"X NM95", "Xn", 12), (u"Y NM95", "Yn", 12), (u"Z NM95", "Zn", 12),
        (u"Id Revit", "Id", 11), (u"UniqueId", "UniqueId", 46),
    ]

    def exporter_excel(rows, resume, chemin):
        import xlsxwriter
        wb = xlsxwriter.Workbook(chemin)
        hdr = wb.add_format({"bold": True, "bg_color": "#2F6496", "font_color": "#FFFFFF",
                             "border": 1, "align": "center", "valign": "vcenter",
                             "text_wrap": True})
        fmts = {}
        for bg in (BG_DIFF, BG_GEOM, BG_GRP_A, BG_GRP_B):
            fmts[bg] = wb.add_format({"bg_color": bg, "border": 1})
        tit = wb.add_format({"bold": True, "font_size": 12, "font_color": "#2F6496"})
        std = fmts[BG_GRP_A]

        ws = wb.add_worksheet(u"Doublons ADN")
        for c, (t, _a, w) in enumerate(COLS):
            ws.set_column(c, c, w)
            ws.write(0, c, t, hdr)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, max(len(rows), 1), len(COLS) - 1)
        for r, row in enumerate(rows, start=1):
            f = fmts.get(row.RowBg, std)
            for c, (_t, a, _w) in enumerate(COLS):
                v = getattr(row, a, u"")
                if a in ("Groupe",):
                    ws.write_number(r, c, v, f)
                else:
                    ws.write_string(r, c, u"{}".format(v), f)

        ws = wb.add_worksheet(u"Resume")
        ws.set_column(0, 0, 58)
        ws.set_column(1, 1, 40)
        ws.write(0, 0, u"Audit des ADN de réservation en double", tit)
        for i, (lib, val) in enumerate(resume, start=2):
            ws.write(i, 0, lib, std)
            if isinstance(val, (int, float)):
                ws.write_number(i, 1, val, std)
            else:
                ws.write_string(i, 1, u"{}".format(val), std)
        wb.close()

    # =========================================================================
    # FENETRE
    # =========================================================================
    def fenetre(rows, resume_ui, resume_xl):
        win = Window()
        win.Title = u"{}  [v{}]".format(TOOL_NAME, SCRIPT_VERSION)
        win.Width = 1450; win.Height = 720; win.MinWidth = 1000; win.MinHeight = 420
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        win.Background = str_ui.couleur("fond")
        root = Grid()
        for h in ("Auto", "Auto", "*", "Auto"):
            root.RowDefinitions.Add(str_ui.rd(h))
        win.Content = root

        titre = str_ui.barre_titre(u"{}  |  {}  |  Revit {}".format(
            TOOL_NAME, doc.Title, app.VersionNumber))
        Grid.SetRow(titre, 0); root.Children.Add(titre)

        fb = Border()
        fb.Background = str_ui.couleur("fond_filtre"); fb.BorderBrush = str_ui.couleur("bordure")
        fb.BorderThickness = Thickness(0, 0, 0, 1); fb.Padding = Thickness(10, 8, 10, 8)
        Grid.SetRow(fb, 1); root.Children.Add(fb)
        col = StackPanel(); fb.Child = col
        for i, (txt, cle) in enumerate(resume_ui):
            tb = TextBlock(); tb.Text = txt; tb.TextWrapping = SW.TextWrapping.Wrap
            tb.FontSize = 12 if i == 0 else 11
            tb.Foreground = str_ui.couleur(cle)
            if i == 0 or cle == "erreur":
                tb.FontWeight = SW.FontWeights.SemiBold
            tb.Margin = Thickness(0, 0 if i == 0 else 3, 0, 0)
            col.Children.Add(tb)

        barre = StackPanel(); barre.Orientation = Orientation.Horizontal
        barre.Margin = Thickness(0, 8, 0, 0); col.Children.Add(barre)
        barre.Children.Add(str_ui.texte(u"Filtre :", marge=Thickness(0, 0, 6, 0)))
        filtres = [F_TOUT, F_DIFF, F_GEOM, F_COPIE, F_PARAM]
        if any(r.Source == SRC_STR for r in rows):
            filtres += [F_RES, F_STR]
        cb = str_ui.combo([(f, f) for f in filtres], 0, 240)
        cb.Margin = Thickness(0, 0, 14, 0); barre.Children.Add(cb)
        barre.Children.Add(str_ui.texte(u"Recherche :", marge=Thickness(0, 0, 6, 0)))
        tb_q = str_ui.zone_texte(240)
        tb_q.ToolTip = u"ADN, n°, famille, type, niveau, demandeur, Id, UniqueId"
        tb_q.Margin = Thickness(0, 0, 14, 0); barre.Children.Add(tb_q)
        lbl = str_ui.texte(u"", gras=True, brush=str_ui.couleur("titre"))
        barre.Children.Add(lbl)

        dg = DataGrid(); dg.Margin = Thickness(8, 8, 8, 4)
        dg.AutoGenerateColumns = False; dg.IsReadOnly = True
        dg.BorderBrush = str_ui.couleur("bordure")
        dg.HeadersVisibility = DataGridHeadersVisibility.Column
        dg.GridLinesVisibility = DataGridGridLinesVisibility.Horizontal
        dg.CanUserResizeColumns = True; dg.CanUserSortColumns = True; dg.FontSize = 12
        dg.SelectionMode = DataGridSelectionMode.Extended
        dg.SelectionUnit = DataGridSelectionUnit.FullRow
        for k, v in ((SystemColors.HighlightBrushKey, "#CFE3F7"),
                     (SystemColors.InactiveSelectionHighlightBrushKey, "#DDEBF8"),
                     (SystemColors.HighlightTextBrushKey, "#1A1A1A"),
                     (SystemColors.InactiveSelectionHighlightTextBrushKey, "#1A1A1A")):
            dg.Resources.Add(k, str_ui.hex_brush(v))
        st_row = SW.Style(DataGridRow)
        st_row.Setters.Add(SW.Setter(DataGridRow.BackgroundProperty, Binding("RowBg")))
        dg.RowStyle = st_row

        def colonne(entete, champ, w, etoile=False, info=None, gras=False):
            c = DataGridTextColumn(); c.Header = entete; c.Binding = Binding(champ)
            c.Width = DataGridLength(w, DataGridLengthUnitType.Star) if etoile else DataGridLength(w)
            st = SW.Style(TextBlock)
            if info:
                st.Setters.Add(SW.Setter(SW.FrameworkElement.ToolTipProperty, Binding(info)))
            if gras:
                st.Setters.Add(SW.Setter(TextBlock.FontWeightProperty, SW.FontWeights.SemiBold))
            st.Setters.Add(SW.Setter(TextBlock.TextTrimmingProperty, SW.TextTrimming.CharacterEllipsis))
            c.ElementStyle = st
            dg.Columns.Add(c)
        if any(r.Source == SRC_STR for r in rows):
            colonne(u"Source", "Source", 55)
        colonne(u"Grp", "Groupe", 45)
        colonne(u"Occ.", "Occ", 45)
        colonne(u"ADN", "Adn", 2, True, info="Adn")
        colonne(u"Verdict groupe", "Verdict", 200, gras=True)
        colonne(u"Paramètres divergents", "Ecarts", 150, info="Ecarts")
        colonne(u"Synchro", "Synchro", 70)
        colonne(u"Écart (cm)", "Ecart", 72)
        colonne(u"Encombr. (cm)", "Dims", 95)
        colonne(u"N° réservation", "Num", 100)
        colonne(u"Famille", "Famille", 1, True, info="Type")
        colonne(u"Niveau", "Niveau", 75)
        colonne(u"Statut VAL", "Statut", 80)
        colonne(u"Suppr.", "Supprime", 50)
        colonne(u"Id", "Id", 70, info="UniqueId")
        Grid.SetRow(dg, 2); root.Children.Add(dg)

        def visibles():
            f = str_ui.valeur_combo(cb, F_TOUT)
            q = (tb_q.Text or u"").strip().lower()
            out = []
            for r in rows:
                if f in (F_DIFF, F_GEOM, F_COPIE) and r.Verdict != f:
                    continue
                if f == F_PARAM and not r.Ecarts:
                    continue
                if f == F_RES and r.Source != SRC_RES:
                    continue
                if f == F_STR and r.Source != SRC_STR:
                    continue
                if q:
                    blob = u" ".join([r.Adn, r.Num, r.Famille, r.Type, r.Niveau, r.Dem,
                                      r.Id, r.UniqueId, r.Statut]).lower()
                    if q not in blob:
                        continue
                out.append(r)
            return out

        def refresh(sender=None, e=None):
            vis = visibles()
            dg.ItemsSource = None
            dg.ItemsSource = vis
            lbl.Text = u"{} ligne(s) affichée(s) sur {}".format(len(vis), len(rows))
        cb.SelectionChanged += refresh
        tb_q.TextChanged += refresh

        footer, sp_btns, grid_f = str_ui.make_footer(SCRIPT_VERSION)
        Grid.SetRow(footer, 3); root.Children.Add(footer)

        def selectionner(s, e):
            sel = list(dg.SelectedItems)
            if not sel:
                MessageBox.Show(u"Sélectionne une ou plusieurs lignes du tableau (Ctrl ou Maj + clic).",
                                TOOL_NAME, MessageBoxButton.OK, MessageBoxImage.Information)
                return
            refs = List[Reference]()
            ids_hote = List[ElementId]()
            for r in sel:
                try:
                    ref = Reference(r._el)
                    if r._link is not None:
                        ref = ref.CreateLinkReference(r._link)
                    else:
                        ids_hote.Add(r._el.Id)
                    refs.Add(ref)
                except Exception:
                    pass
            try:
                uidoc.Selection.SetReferences(refs)
            except Exception:
                try:
                    uidoc.Selection.SetElementIds(ids_hote)
                except Exception:
                    pass
            try:
                if ids_hote.Count:
                    uidoc.ShowElements(ids_hote)
            except Exception:
                pass
            win.Close()

        def exporter(s, e):
            try:
                clr.AddReference("System.Windows.Forms")
                from System.Windows.Forms import SaveFileDialog, DialogResult
                dlg = SaveFileDialog()
                dlg.Title = u"Exporter l'audit des ADN"
                dlg.Filter = u"Classeur Excel (*.xlsx)|*.xlsx"
                dlg.FileName = u"Audit_doublons_ADN.xlsx"
                prefs = _load_prefs()
                last = prefs.get("last_folder", u"")
                if last and os.path.isdir(last):
                    dlg.InitialDirectory = last
                if dlg.ShowDialog() != DialogResult.OK or not dlg.FileName:
                    return
                exporter_excel(visibles(), resume_xl, dlg.FileName)
                prefs["last_folder"] = os.path.dirname(dlg.FileName)
                _save_prefs(prefs)
                MessageBox.Show(u"Export réussi :\n{}".format(dlg.FileName), TOOL_NAME,
                                MessageBoxButton.OK, MessageBoxImage.Information)
            except Exception as ex:
                MessageBox.Show(u"Erreur d'export :\n{}".format(ex), TOOL_NAME,
                                MessageBoxButton.OK, MessageBoxImage.Error)

        sp_btns.Children.Add(str_ui.footer_btn(u"Fermer", "fermer", 90, lambda s, e: win.Close()))
        bs = str_ui.footer_btn(u"Sélectionner dans Revit", "secondaire", 170, selectionner)
        bs.Foreground = str_ui.couleur("texte"); bs.Margin = Thickness(6, 0, 0, 0)
        sp_btns.Children.Add(bs)
        be = str_ui.footer_btn(u"Exporter Excel", "exporter", 130, exporter)
        be.Margin = Thickness(6, 0, 0, 0)
        sp_btns.Children.Add(be)
        grid_f.Children.Add(sp_btns)

        refresh()
        return win

    # =========================================================================
    # MAIN
    # =========================================================================
    def _ligne_source(nom, s):
        return (u"{} : {} réservation(s) analysée(s) — {} ADN en doublon ({} objets, {} en trop) — "
                u"{} à positions différentes, {} géométrie différente, {} copies superposées").format(
                    nom, s["analysees"], s["adn_dbl"], s["objets"], s["en_trop"],
                    s["diff"], s["geom"], s["copie"])

    def main():
        lnk = None
        if fgh_res.est_maquette_res(doc):
            rdoc, nom_res = doc, doc.Title
        else:
            lnk = find_link_instance()
            if lnk is None or lnk.GetLinkDocument() is None:
                MessageBox.Show(
                    u"Ouvre la maquette RES ({0}…), ou lance l'outil depuis la STR avec le lien "
                    u"« {0}… » chargé.".format(LINK_MODEL_PREFIX),
                    TOOL_NAME, MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            rdoc, nom_res = lnk.GetLinkDocument(), fgh_res.nom(lnk)

        s_res = _stats_vides()
        rows, grp = analyser(collect_res(rdoc, s_res), SRC_RES, s_res, lnk, 0)

        s_str = None
        if lnk is not None:
            s_str = _stats_vides()
            rows_h, grp = analyser(collect_str(s_str), SRC_STR, s_str, None, grp)
            rows += rows_h

        if s_res["analysees"] == 0 and not (s_str and s_str["analysees"]):
            MessageBox.Show(u"Aucune réservation FGH_RES à analyser.", TOOL_NAME,
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return
        rows.sort(key=lambda r: r.Tri)

        resume_ui = [(_ligne_source(u"Maquette RES « {} »".format(nom_res), s_res), "titre")]
        if s_str is not None:
            resume_ui.append((_ligne_source(u"Maquette STR (copies)", s_str), "texte"))
        resume_ui.append((
            u"Hors synchro (non analysées) — RES : {} sans ADN, {} ADN sans _STATUT, {} mutualisée(s)"
            u"{}.   Tolérances : position {:.0f} cm, encombrement {:.0f} cm.".format(
                s_res["sans_adn"], s_res["sans_statut"], s_res["mutualisees"],
                u"   |   STR : {} sans ADN".format(s_str["sans_adn"]) if s_str else u"",
                TOL_POSITION_CM, TOL_DIM_CM), "texte_gris"))
        perdues = s_res["diff"] + (s_str["diff"] if s_str else 0)
        total_dbl = s_res["adn_dbl"] + (s_str["adn_dbl"] if s_str else 0)
        if perdues:
            resume_ui.append((
                u"⚠ {} groupe(s) à positions différentes : réservations distinctes sous un même ADN, "
                u"la synchro ne compare que celle marquée « Retenue » (dernière lue), les autres "
                u"sont perdues.".format(perdues), "erreur"))
        elif total_dbl:
            resume_ui.append((
                u"Aucun groupe à positions différentes : les doublons sont superposés. Un exemplaire "
                u"par groupe est en trop ; vérifier les paramètres divergents avant de supprimer.",
                "avertissement"))
        else:
            resume_ui.append((u"Aucun ADN en double : chaque réservation est vue par la synchro.", "ok"))

        resume_xl = [
            (u"Date de l'audit", System.DateTime.Now.ToString("dd/MM/yyyy HH:mm")),
            (u"Maquette ouverte", doc.Title),
            (u"Maquette RES analysée", nom_res),
            (u"", u""),
        ]
        for nom, s in ((u"RES", s_res), (u"STR", s_str)):
            if s is None:
                continue
            resume_xl += [
                (u"{} — réservations FGH_RES".format(nom), s["familles"]),
                (u"{} — réservations analysées (filtre Sync)".format(nom), s["analysees"]),
                (u"{} — sans ADN".format(nom), s["sans_adn"]),
            ]
            if nom == u"RES":
                resume_xl += [(u"RES — ADN sans _STATUT", s["sans_statut"]),
                              (u"RES — mutualisées", s["mutualisees"])]
            resume_xl += [
                (u"{} — ADN en doublon".format(nom), s["adn_dbl"]),
                (u"{} — objets concernés".format(nom), s["objets"]),
                (u"{} — objets en trop".format(nom), s["en_trop"]),
                (u"{} — groupes à positions différentes".format(nom), s["diff"]),
                (u"{} — groupes même position, géométrie différente".format(nom), s["geom"]),
                (u"{} — groupes copies superposées".format(nom), s["copie"]),
                (u"{} — groupes aux paramètres divergents".format(nom), s["param"]),
                (u"", u""),
            ]
        resume_xl += [(u"Tolérance de position (cm)", TOL_POSITION_CM),
                      (u"Tolérance d'encombrement (cm)", TOL_DIM_CM),
                      (u"Réservations perdues par la synchro (groupes à positions différentes)",
                       perdues)]

        fenetre(rows, resume_ui, resume_xl).ShowDialog()

    main()

except Exception as _ex:
    _show_fatal(u"{} v{} — Erreur de chargement".format(TOOL_NAME, SCRIPT_VERSION), _ex)
