# -*- coding: utf-8 -*-
"""pyRevit bridge: read-only HTTP endpoints the assistant uses to look at the
model that is open in Revit.

Install: pyRevit tab > Settings > Custom Extension Directories > add the
`revit` folder of this project; then pyRevit Settings > Routes > enable the
Routes server (default port 48884) and restart Revit.

Endpoints (GET): http://127.0.0.1:48884/assistant/<model|categories|warnings|levels|sheets|links>
"""
from collections import Counter

from pyrevit import DB, routes

api = routes.API("assistant")
FEET_TO_M = 0.3048


def _no_model():
    return routes.make_response(data={"error": "No model is open in Revit."}, status=404)


@api.route("/model")
def model(doc):
    if doc is None:
        return _no_model()
    info = doc.ProjectInformation
    return routes.make_response(data={
        "title": doc.Title,
        "path": doc.PathName,
        "revit_version": doc.Application.VersionName,
        "workshared": doc.IsWorkshared,
        "project_name": info.Name if info else "",
        "project_number": info.Number if info else "",
        "client": info.ClientName if info else "",
    })


@api.route("/categories")
def categories(doc):
    if doc is None:
        return _no_model()
    counts = Counter()
    for el in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        if el.Category is not None:
            counts[el.Category.Name] += 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:60]
    return routes.make_response(data={
        "total_elements": sum(counts.values()),
        "by_category": [{"category": k, "count": v} for k, v in top],
    })


@api.route("/warnings")
def warnings(doc):
    if doc is None:
        return _no_model()
    counts = Counter(w.GetDescriptionText() for w in doc.GetWarnings())
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:15]
    return routes.make_response(data={
        "total_warnings": sum(counts.values()),
        "most_common": [{"warning": k, "count": v} for k, v in top],
    })


@api.route("/levels")
def levels(doc):
    if doc is None:
        return _no_model()
    items = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.Elevation)
    return routes.make_response(data={"levels": [
        {"name": l.Name, "elevation_m": round(l.Elevation * FEET_TO_M, 3)} for l in items]})


@api.route("/sheets")
def sheets(doc):
    if doc is None:
        return _no_model()
    items = [s for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet) if not s.IsPlaceholder]
    items.sort(key=lambda s: s.SheetNumber)
    return routes.make_response(data={
        "total_sheets": len(items),
        "sheets": [{"number": s.SheetNumber, "name": s.Name} for s in items[:200]],
    })


@api.route("/links")
def links(doc):
    if doc is None:
        return _no_model()
    items = DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance)
    return routes.make_response(data={"links": sorted(l.Name for l in items)})
