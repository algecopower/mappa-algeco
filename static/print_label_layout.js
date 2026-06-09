(function(root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.PrintLabelLayout = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
    'use strict';

    const DEFAULTS = {
        padding: 10,
        labelSpacing: 2,
        ownMarkerSpacing: 0,
        markerSpacing: 1,
        maxDistance: 36,
        maxLineLength: 45,
        emergencyMaxDistance: 72,
        emergencyMaxLineLength: 72,
        longDistancePenalty: 24,
        maxCandidates: 104,
        beamWidth: 6,
        visibleLineLength: 16,
        markerGapPenalty: 6,
        touchBonus: 35,
        axisOffsetPenalty: 0.5,
        shiftedCardinalPenalty: 4,
        bottomDirectionPenalty: 8,
        sideDirectionPenalty: 40,
        diagonalPenalty: 90,
        shortDiagonalPenalty: 120,
        candidateSeedsPerDirection: 4,
        crossingPenalty: 900,
        lineThroughLabelPenalty: 350,
        overlapPenalty: 5000,
        componentDistance: 105,
        beamComponentLimit: 12
    };

    function boxesOverlap(a, b, spacing) {
        return a.x < b.x + b.width + spacing &&
            a.x + a.width + spacing > b.x &&
            a.y < b.y + b.height + spacing &&
            a.y + a.height + spacing > b.y;
    }

    function segmentsIntersect(a, b) {
        const d1 = (b.x2 - b.x1) * (a.y1 - b.y1) - (b.y2 - b.y1) * (a.x1 - b.x1);
        const d2 = (b.x2 - b.x1) * (a.y2 - b.y1) - (b.y2 - b.y1) * (a.x2 - b.x1);
        const d3 = (a.x2 - a.x1) * (b.y1 - a.y1) - (a.y2 - a.y1) * (b.x1 - a.x1);
        const d4 = (a.x2 - a.x1) * (b.y2 - a.y1) - (a.y2 - a.y1) * (b.x2 - a.x1);

        return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
            ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
    }

    function lineIntersectsBox(line, box, padding) {
        const expanded = {
            x: box.x - padding,
            y: box.y - padding,
            width: box.width + padding * 2,
            height: box.height + padding * 2
        };
        const inside = (x, y) =>
            x > expanded.x && x < expanded.x + expanded.width &&
            y > expanded.y && y < expanded.y + expanded.height;

        if (inside(line.x1, line.y1) || inside(line.x2, line.y2)) return true;

        const left = expanded.x;
        const right = expanded.x + expanded.width;
        const top = expanded.y;
        const bottom = expanded.y + expanded.height;
        const edges = [
            { x1: left, y1: top, x2: right, y2: top },
            { x1: right, y1: top, x2: right, y2: bottom },
            { x1: right, y1: bottom, x2: left, y2: bottom },
            { x1: left, y1: bottom, x2: left, y2: top }
        ];
        return edges.some(edge => segmentsIntersect(line, edge));
    }

    function connectorLine(item, x, y) {
        const width = item.anchorWidth;
        const height = item.anchorHeight;
        let startX;
        let startY;

        if (item.markerX > x + width) {
            startX = x + width;
            startY = Math.max(y + 5, Math.min(y + height - 5, item.markerY));
        } else if (item.markerX < x) {
            startX = x;
            startY = Math.max(y + 5, Math.min(y + height - 5, item.markerY));
        } else if (item.markerY < y) {
            startX = Math.max(x + 5, Math.min(x + width - 5, item.markerX));
            startY = y;
        } else {
            startX = Math.max(x + 5, Math.min(x + width - 5, item.markerX));
            startY = y + height;
        }

        const target = item.markerTargetBox;
        if (!target) {
            return { x1: startX, y1: startY, x2: item.markerX, y2: item.markerY };
        }

        return {
            x1: startX,
            y1: startY,
            x2: Math.max(target.x, Math.min(target.x + target.width, startX)),
            y2: Math.max(target.y, Math.min(target.y + target.height, startY))
        };
    }

    function lineLength(line) {
        return Math.hypot(line.x2 - line.x1, line.y2 - line.y1);
    }

    function footprintFor(item, x, y) {
        return {
            x: x + item.footprintOffsetX,
            y: y + item.footprintOffsetY,
            width: item.footprintWidth,
            height: item.footprintHeight
        };
    }

    function boxGap(a, b) {
        const dx = Math.max(b.x - (a.x + a.width), a.x - (b.x + b.width), 0);
        const dy = Math.max(b.y - (a.y + a.height), a.y - (b.y + b.height), 0);
        return Math.hypot(dx, dy);
    }

    function uncheckedCandidate(item, x, y, kind, options, meta) {
        meta = meta || {};
        const footprint = footprintFor(item, x, y);
        const line = connectorLine(item, x, y);
        const distance = lineLength(line);
        const markerGap = item.markerBox ? boxGap(footprint, item.markerBox) : distance;
        const outlierPenalty = Math.max(0, distance - 32) * 3 +
            Math.max(0, distance - 42) * 10;
        const longDistancePenalty = Math.max(
            0, distance - options.maxLineLength
        ) * options.longDistancePenalty;
        const axisOffset = Math.abs(meta.axisOffset || 0);
        const shiftedPenalty = kind === 'cardinal' && axisOffset > 2
            ? options.shiftedCardinalPenalty + axisOffset * options.axisOffsetPenalty
            : 0;
        let directionPenalty = 0;
        if (kind === 'cardinal') {
            if (meta.direction === 'bottom') {
                directionPenalty = options.bottomDirectionPenalty;
            } else if (meta.direction === 'left' || meta.direction === 'right') {
                directionPenalty = options.sideDirectionPenalty;
            }
        }
        const diagonalPenalty = kind === 'diagonal'
            ? options.diagonalPenalty +
                (distance < options.visibleLineLength ? options.shortDiagonalPenalty : 0)
            : 0;
        const touchBonus = markerGap <= 1 ? options.touchBonus : 0;

        return {
            x,
            y,
            kind,
            direction: meta.direction || kind,
            axisOffset,
            markerGap,
            footprint,
            line,
            visibleLine: distance >= options.visibleLineLength,
            distance,
            baseCost: distance + outlierPenalty + longDistancePenalty +
                markerGap * options.markerGapPenalty +
                shiftedPenalty + directionPenalty + diagonalPenalty - touchBonus
        };
    }

    function makeCandidate(item, x, y, kind, markerPositions, mapSize, options, meta) {
        const candidate = uncheckedCandidate(item, x, y, kind, options, meta);
        const footprint = candidate.footprint;
        const padding = options.padding;
        if (candidate.distance > options.emergencyMaxLineLength) return null;
        if (footprint.x < padding || footprint.y < padding ||
            footprint.x + footprint.width > mapSize.width - padding ||
            footprint.y + footprint.height > mapSize.height - padding) {
            return null;
        }
        if (markerPositions.some(marker => {
            const spacing = String(marker.id) === String(item.id)
                ? options.ownMarkerSpacing
                : options.markerSpacing;
            return boxesOverlap(footprint, marker, spacing);
        })) {
            return null;
        }
        return candidate;
    }

    function buildCandidates(item, markerPositions, mapSize, options) {
        const candidates = [];
        const seen = new Set();
        const snap = value => Math.round(value * 10) / 10;
        const add = (x, y, kind, meta) => {
            x = snap(x);
            y = snap(y);
            const key = `${x}:${y}`;
            if (seen.has(key)) return;
            seen.add(key);
            const candidate = makeCandidate(
                item, x, y, kind, markerPositions, mapSize, options, meta
            );
            if (candidate) candidates.push(candidate);
        };

        const horizontalShifts = [];
        const verticalShifts = [];
        const maxHorizontalShift = Math.round(item.anchorWidth * 1.15);
        const maxVerticalShift = Math.round(item.anchorHeight * 1.45);
        for (let shift = -maxHorizontalShift; shift <= maxHorizontalShift; shift += 10) {
            horizontalShifts.push(shift);
        }
        for (let shift = -maxVerticalShift; shift <= maxVerticalShift; shift += 8) {
            verticalShifts.push(shift);
        }
        horizontalShifts.push(0);
        verticalShifts.push(0);

        const ownMarker = markerPositions.find(
            marker => String(marker.id) === String(item.id)
        );
        const markerLeft = ownMarker ? ownMarker.x : item.markerX;
        const markerTop = ownMarker ? ownMarker.y : item.markerY;
        const markerRight = ownMarker
            ? ownMarker.x + ownMarker.width
            : item.markerX;
        const markerBottom = ownMarker
            ? ownMarker.y + ownMarker.height
            : item.markerY;
        const centeredX = item.markerX - item.footprintOffsetX -
            item.footprintWidth / 2;
        const centeredY = item.markerY - item.footprintOffsetY -
            item.footprintHeight / 2;
        const aboveY = gap =>
            markerTop - gap - item.footprintOffsetY - item.footprintHeight;
        const belowY = gap => markerBottom + gap - item.footprintOffsetY;
        const rightX = gap => markerRight + gap - item.footprintOffsetX;
        const leftX = gap =>
            markerLeft - gap - item.footprintOffsetX - item.footprintWidth;

        for (let gap = 0; gap <= options.emergencyMaxDistance; gap += 4) {
            horizontalShifts.forEach(shift => {
                add(
                    centeredX + shift,
                    aboveY(gap),
                    'cardinal',
                    { direction: 'top', axisOffset: shift }
                );
                add(
                    centeredX + shift,
                    belowY(gap),
                    'cardinal',
                    { direction: 'bottom', axisOffset: shift }
                );
            });
            verticalShifts.forEach(shift => {
                add(
                    rightX(gap),
                    centeredY + shift,
                    'cardinal',
                    { direction: 'right', axisOffset: shift }
                );
                add(
                    leftX(gap),
                    centeredY + shift,
                    'cardinal',
                    { direction: 'left', axisOffset: shift }
                );
            });

            add(
                rightX(gap), aboveY(gap),
                'diagonal', { direction: 'top-right' }
            );
            add(
                rightX(gap), belowY(gap),
                'diagonal', { direction: 'bottom-right' }
            );
            add(
                leftX(gap), aboveY(gap),
                'diagonal',
                { direction: 'top-left' }
            );
            add(
                leftX(gap), belowY(gap),
                'diagonal',
                { direction: 'bottom-left' }
            );
        }

        candidates.sort((a, b) =>
            a.baseCost - b.baseCost || a.y - b.y || a.x - b.x);

        const selected = [];
        const selectedKeys = new Set();
        const keep = candidate => {
            const key = `${candidate.x}:${candidate.y}`;
            if (selectedKeys.has(key)) return;
            selectedKeys.add(key);
            selected.push(candidate);
        };
        const directions = [
            'top', 'bottom', 'left', 'right',
            'top-left', 'top-right', 'bottom-left', 'bottom-right'
        ];
        directions.forEach(direction => {
            candidates
                .filter(candidate => candidate.direction === direction)
                .slice(0, options.candidateSeedsPerDirection)
                .forEach(keep);
        });
        candidates.forEach(keep);

        return selected.slice(0, options.maxCandidates);
    }

    function alignmentBonus(candidate, boxes) {
        const centerX = candidate.footprint.x + candidate.footprint.width / 2;
        const centerY = candidate.footprint.y + candidate.footprint.height / 2;
        let bonus = 0;

        boxes.forEach(box => {
            const otherCenterX = box.x + box.width / 2;
            const otherCenterY = box.y + box.height / 2;
            if (Math.abs(candidate.footprint.y - box.y) <= 3) bonus = Math.max(bonus, 6);
            if (Math.abs(centerY - otherCenterY) <= 3) bonus = Math.max(bonus, 5);
            if (Math.abs(centerX - otherCenterX) <= 3) bonus = Math.max(bonus, 2);
        });
        return bonus;
    }

    function candidateConflictCost(candidate, state, options) {
        let crossings = 0;
        let labelHits = 0;

        if (candidate.visibleLine) {
            state.lines.forEach(line => {
                if (segmentsIntersect(candidate.line, line)) crossings += 1;
            });
            state.boxes.forEach(box => {
                if (lineIntersectsBox(candidate.line, box, 1)) labelHits += 1;
            });
        }

        state.lines.forEach(line => {
            if (lineIntersectsBox(line, candidate.footprint, 1)) labelHits += 1;
        });

        return crossings * options.crossingPenalty +
            labelHits * options.lineThroughLabelPenalty;
    }

    function sortPrepared(entries) {
        entries.sort((a, b) =>
            b.density - a.density ||
            a.candidates.length - b.candidates.length ||
            (b.item.footprintWidth * b.item.footprintHeight) -
                (a.item.footprintWidth * a.item.footprintHeight) ||
            a.originalIndex - b.originalIndex);
    }

    function fallbackCandidate(entry, options) {
        const marker = entry.item.markerBox;
        if (marker) {
            return uncheckedCandidate(
                entry.item,
                marker.x + marker.width + options.ownMarkerSpacing -
                    entry.item.footprintOffsetX,
                entry.item.markerY - entry.item.footprintOffsetY -
                    entry.item.footprintHeight / 2,
                'fallback',
                options,
                { direction: 'right' }
            );
        }
        return uncheckedCandidate(
            entry.item,
            entry.item.markerX + 18,
            entry.item.markerY - entry.item.anchorHeight / 2,
            'fallback',
            options
        );
    }

    function solveComponentBeam(entries, options) {
        sortPrepared(entries);
        let beam = [{
            cost: 0,
            placements: Object.create(null),
            boxes: [],
            lines: []
        }];

        entries.forEach(entry => {
            const expanded = [];
            beam.forEach(state => {
                entry.candidates.forEach(candidate => {
                    if (state.boxes.some(box =>
                        boxesOverlap(candidate.footprint, box, options.labelSpacing))) {
                        return;
                    }

                    const conflictCost = candidateConflictCost(candidate, state, options);
                    const cost = state.cost + candidate.baseCost + conflictCost -
                        alignmentBonus(candidate, state.boxes);
                    const placements = Object.assign(
                        Object.create(null), state.placements
                    );
                    placements[entry.item.id] = candidate;
                    expanded.push({
                        cost,
                        placements,
                        boxes: state.boxes.concat(candidate.footprint),
                        lines: candidate.visibleLine
                            ? state.lines.concat(candidate.line)
                            : state.lines
                    });
                });
            });

            if (expanded.length === 0 && entry.candidates.length > 0) {
                beam.forEach(state => {
                    entry.candidates.forEach(candidate => {
                        const overlaps = state.boxes.filter(box =>
                            boxesOverlap(candidate.footprint, box, options.labelSpacing)
                        ).length;
                        const conflictCost = candidateConflictCost(candidate, state, options);
                        const cost = state.cost + candidate.baseCost + conflictCost +
                            overlaps * options.overlapPenalty;
                        const placements = Object.assign(
                            Object.create(null), state.placements
                        );
                        placements[entry.item.id] = candidate;
                        expanded.push({
                            cost,
                            placements,
                            boxes: state.boxes.concat(candidate.footprint),
                            lines: candidate.visibleLine
                                ? state.lines.concat(candidate.line)
                                : state.lines
                        });
                    });
                });
            }

            if (expanded.length === 0) {
                const candidate = fallbackCandidate(entry, options);
                beam.forEach(state => {
                    const placements = Object.assign(
                        Object.create(null), state.placements
                    );
                    placements[entry.item.id] = candidate;
                    expanded.push({
                        cost: state.cost + candidate.baseCost + options.crossingPenalty * 2,
                        placements,
                        boxes: state.boxes.concat(candidate.footprint),
                        lines: candidate.visibleLine
                            ? state.lines.concat(candidate.line)
                            : state.lines
                    });
                });
            }

            expanded.sort((a, b) => a.cost - b.cost);
            beam = expanded.slice(0, options.beamWidth);
        });

        return beam.length > 0 ? beam[0].placements : Object.create(null);
    }

    function solveComponentGreedy(entries, options) {
        sortPrepared(entries);
        const placements = Object.create(null);
        const state = { boxes: [], lines: [] };

        entries.forEach(entry => {
            const candidates = entry.candidates.length > 0
                ? entry.candidates
                : [fallbackCandidate(entry, options)];
            let best = null;

            candidates.forEach(candidate => {
                const overlaps = state.boxes.filter(box =>
                    boxesOverlap(candidate.footprint, box, options.labelSpacing)
                ).length;
                const conflictCost = candidateConflictCost(candidate, state, options);
                const cost = candidate.baseCost + conflictCost +
                    overlaps * options.overlapPenalty -
                    alignmentBonus(candidate, state.boxes);

                if (!best || cost < best.cost) {
                    best = { candidate, cost, overlaps, conflictCost };
                }
                if (overlaps === 0 && conflictCost === 0) {
                    best = { candidate, cost, overlaps, conflictCost };
                    return;
                }
            });

            const chosen = best ? best.candidate : fallbackCandidate(entry, options);
            placements[entry.item.id] = chosen;
            state.boxes.push(chosen.footprint);
            if (chosen.visibleLine) state.lines.push(chosen.line);
        });

        return placements;
    }

    function buildComponents(prepared, options) {
        const components = [];
        const used = new Set();
        const threshold = options.componentDistance;

        prepared.forEach((entry, idx) => {
            if (used.has(idx)) return;

            const component = [];
            const queue = [idx];
            used.add(idx);

            while (queue.length > 0) {
                const currentIdx = queue.shift();
                const current = prepared[currentIdx];
                component.push(current);

                prepared.forEach((other, otherIdx) => {
                    if (used.has(otherIdx)) return;
                    const dist = Math.hypot(
                        current.item.markerX - other.item.markerX,
                        current.item.markerY - other.item.markerY
                    );
                    if (dist <= threshold) {
                        used.add(otherIdx);
                        queue.push(otherIdx);
                    }
                });
            }

            components.push(component);
        });

        components.sort((a, b) => b.length - a.length);
        return components;
    }

    function markerConflictCount(candidate, currentId, markerPositions, options) {
        return markerPositions.filter(marker => {
            const spacing = String(marker.id) === String(currentId)
                ? options.ownMarkerSpacing
                : options.markerSpacing;
            return boxesOverlap(candidate.footprint, marker, spacing);
        }).length;
    }

    function placementConflictCost(
        candidate, placements, currentId, markerPositions, options
    ) {
        let overlaps = 0;
        let crossings = 0;
        let labelHits = 0;

        Object.keys(placements).forEach(otherId => {
            if (otherId === currentId) return;

            const other = placements[otherId];
            if (boxesOverlap(candidate.footprint, other.footprint, options.labelSpacing)) {
                overlaps += 1;
            }
            if (candidate.visibleLine && other.visibleLine &&
                segmentsIntersect(candidate.line, other.line)) {
                crossings += 1;
            }
            if (candidate.visibleLine &&
                lineIntersectsBox(candidate.line, other.footprint, 1)) {
                labelHits += 1;
            }
            if (other.visibleLine &&
                lineIntersectsBox(other.line, candidate.footprint, 1)) {
                labelHits += 1;
            }
        });

        return overlaps * options.overlapPenalty +
            markerConflictCount(
                candidate, currentId, markerPositions, options
            ) * options.overlapPenalty * 2 +
            crossings * options.crossingPenalty +
            labelHits * options.lineThroughLabelPenalty;
    }

    function repairPlacements(placements, prepared, markerPositions, options) {
        for (let pass = 0; pass < 4; pass += 1) {
            let changed = false;

            prepared.forEach(entry => {
                const id = entry.item.id;
                const current = placements[id] || fallbackCandidate(entry, options);
                const currentConflict = placementConflictCost(
                    current, placements, id, markerPositions, options
                );
                if (currentConflict === 0) return;

                let best = current;
                let bestConflict = currentConflict;
                let bestCost = current.baseCost + currentConflict;
                const candidates = entry.candidates.length > 0
                    ? entry.candidates
                    : [fallbackCandidate(entry, options)];

                candidates.forEach(candidate => {
                    const conflict = placementConflictCost(
                        candidate, placements, id, markerPositions, options
                    );
                    const cost = candidate.baseCost + conflict;
                    if (conflict < bestConflict ||
                        (conflict === bestConflict && cost < bestCost)) {
                        best = candidate;
                        bestConflict = conflict;
                        bestCost = cost;
                    }
                });

                if (best !== current) {
                    placements[id] = best;
                    changed = true;
                }
            });

            if (!changed) break;
        }

        return placements;
    }

    function solveLabelLayout(items, markerPositions, mapSize, customOptions) {
        const options = Object.assign({}, DEFAULTS, customOptions || {});
        const prepared = items.map((item, originalIndex) => {
            const normalized = Object.assign({
                anchorWidth: item.width,
                anchorHeight: item.height,
                footprintWidth: item.width,
                footprintHeight: item.height,
                footprintOffsetX: 0,
                footprintOffsetY: 0
            }, item);
            if (!normalized.markerTargetBox) {
                normalized.markerTargetBox = markerPositions.find(
                    marker => String(marker.id) === String(normalized.id)
                );
            }
            normalized.markerBox = markerPositions.find(
                marker => String(marker.id) === String(normalized.id)
            );
            const candidates = buildCandidates(
                normalized, markerPositions, mapSize, options
            );
            const density = markerPositions.filter(marker => {
                const centerX = marker.x + marker.width / 2;
                const centerY = marker.y + marker.height / 2;
                return Math.hypot(
                    centerX - normalized.markerX,
                    centerY - normalized.markerY
                ) < 125;
            }).length;
            return { item: normalized, candidates, density, originalIndex };
        });

        const placements = Object.create(null);
        const components = buildComponents(prepared, options);
        components.forEach(component => {
            const componentPlacements = component.length > options.beamComponentLimit
                ? solveComponentGreedy(component, options)
                : solveComponentBeam(component, options);
            Object.assign(placements, componentPlacements);
        });

        return repairPlacements(placements, prepared, markerPositions, options);
    }

    return {
        boxesOverlap,
        boxGap,
        connectorLine,
        lineLength,
        segmentsIntersect,
        solveLabelLayout
    };
});
