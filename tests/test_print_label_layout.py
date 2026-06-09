# -*- coding: utf-8 -*-
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_layout_stampa_mantiene_compatta_la_tappa_15():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js non disponibile")

    script = textwrap.dedent(
        """
        const assert = require('assert');
        const layout = require('./static/print_label_layout.js');

        // Geometria relativa del gruppo SIDI del 09/06/2026 (tappe 10-16).
        const points = [
            ['10', 560, 289],
            ['11', 319, 239],
            ['12', 352, 365],
            ['13', 358, 358],
            ['14', 535, 348],
            ['15', 414, 323],
            ['16', 942, -230]
        ];
        const markers = points.map(([id, x, y]) => ({
            id, x: x - 15, y: y - 15, width: 30, height: 30
        }));
        const items = points.map(([id, x, y]) => ({
            id,
            markerX: x,
            markerY: y,
            width: 60,
            height: 34,
            footprintWidth: 66,
            footprintHeight: 41
        }));

        const result = layout.solveLabelLayout(
            items, markers, { width: 900, height: 650 }
        );
        points.forEach(([id]) => assert(result[id], `Etichetta ${id} assente`));

        assert(
            result['15'].distance < 45,
            `La tappa 15 e' ancora troppo distante: ${result['15'].distance}px`
        );

        const visibleIds = ['10', '11', '12', '13', '14', '15'];
        for (let i = 0; i < visibleIds.length; i += 1) {
            for (let j = i + 1; j < visibleIds.length; j += 1) {
                const a = result[visibleIds[i]].footprint;
                const b = result[visibleIds[j]].footprint;
                assert(
                    !layout.boxesOverlap(a, b, 3),
                    `Sovrapposizione ${visibleIds[i]}-${visibleIds[j]}`
                );
                assert(
                    !layout.segmentsIntersect(
                        result[visibleIds[i]].line,
                        result[visibleIds[j]].line
                    ),
                    `Incrocio ${visibleIds[i]}-${visibleIds[j]}`
                );
            }
        }
        """
    )

    subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_layout_stampa_limita_collegamenti_con_marker_quasi_coincidenti():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js non disponibile")

    script = textwrap.dedent(
        """
        const assert = require('assert');
        const layout = require('./static/print_label_layout.js');

        // Estratto dal rendering reale SIDI 09/06/2026: le tappe 12-16
        // sono quasi coincidenti in pixel e non devono cadere sul fallback.
        const points = [
            ['11', 462, 325],
            ['12', 462, 327],
            ['13', 462, 327],
            ['14', 465, 327],
            ['15', 463, 326],
            ['16', 470, 319]
        ];
        const markers = points.map(([id, x, y]) => ({
            id, x: x - 15, y: y - 15, width: 30, height: 30
        }));
        const items = points.map(([id, x, y]) => ({
            id,
            markerX: x,
            markerY: y,
            width: 52.5,
            height: 31.5,
            footprintWidth: 56.5,
            footprintHeight: 36.5
        }));

        const result = layout.solveLabelLayout(
            items, markers, { width: 1179, height: 640 }
        );

        points.forEach(([id]) => assert(result[id], `Etichetta ${id} assente`));
        points.forEach(([id]) => {
            assert(
                result[id].distance <= 45,
                `La tappa ${id} e' troppo distante: ${result[id].distance}px`
            );
        });

        let overlaps = 0;
        let crossings = 0;
        for (let i = 0; i < points.length; i += 1) {
            for (let j = i + 1; j < points.length; j += 1) {
                const idA = points[i][0];
                const idB = points[j][0];
                if (layout.boxesOverlap(
                    result[idA].footprint, result[idB].footprint, 2
                )) overlaps += 1;
                if (layout.segmentsIntersect(
                    result[idA].line, result[idB].line
                )) crossings += 1;
            }
        }
        assert(overlaps <= 2, `Troppe sovrapposizioni inevitabili: ${overlaps}`);
        assert.strictEqual(crossings, 0, `Incroci rilevati: ${crossings}`);
        """
    )

    subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_layout_stampa_preferisce_cardinale_quando_c_e_spazio():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js non disponibile")

    script = textwrap.dedent(
        """
        const assert = require('assert');
        const layout = require('./static/print_label_layout.js');

        const markers = [{id: '0', x: 284, y: 284, width: 32, height: 32}];
        const items = [{
            id: '0',
            markerX: 300,
            markerY: 300,
            width: 52,
            height: 30,
            anchorWidth: 52,
            anchorHeight: 30,
            footprintWidth: 56,
            footprintHeight: 36,
            footprintOffsetX: 0,
            footprintOffsetY: 0
        }];

        const result = layout.solveLabelLayout(
            items, markers, { width: 700, height: 700 }
        );

        assert.strictEqual(result['0'].kind, 'cardinal');
        assert.strictEqual(result['0'].direction, 'top');
        assert.strictEqual(result['0'].axisOffset, 0);
        assert(
            Math.abs(
                result['0'].footprint.x +
                result['0'].footprint.width / 2 -
                items[0].markerX
            ) <= 1,
            'L ingombro visivo non e centrato sopra il marker'
        );
        assert(
            result['0'].distance < 16,
            `La label dovrebbe restare senza tratteggio: ${result['0'].distance}px`
        );
        """
    )

    subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_layout_stampa_viewport_piccolo_non_copre_cluster_e_marker():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js non disponibile")

    script = textwrap.dedent(
        """
        const assert = require('assert');
        const layout = require('./static/print_label_layout.js');

        // Geometria reale SIDI 10/06/2026, viewport mappa 799x522 a zoom 17.5.
        // I primi due elementi sono i cluster 739/674 e 232/208/186; il terzo
        // e' il marker 264 che non deve essere coperto dalla lista a tre righe.
        const markers = [
            {id: '0', x: 446, y: 97, width: 37, height: 37},
            {id: '1', x: 471, y: 90, width: 37, height: 37},
            {id: '2', x: 497, y: 118, width: 28, height: 28},
            {id: '3', x: 607, y: 156, width: 28, height: 28}
        ];
        const targetBoxes = [
            {x: 448, y: 104, width: 28, height: 28},
            {x: 473, y: 97, width: 28, height: 28},
            {x: 499, y: 120, width: 24, height: 24},
            {x: 609, y: 158, width: 24, height: 24}
        ];
        const centers = [
            [462, 118], [487, 111], [511, 132], [621, 170]
        ];
        const dimensions = [
            [94.2, 53], [94.2, 78], [52.5, 31.5], [52.5, 31.5]
        ];
        const items = centers.map(([x, y], idx) => ({
            id: String(idx),
            markerX: x,
            markerY: y,
            markerTargetBox: targetBoxes[idx],
            width: dimensions[idx][0],
            height: dimensions[idx][1],
            anchorWidth: dimensions[idx][0],
            anchorHeight: dimensions[idx][1],
            footprintWidth: dimensions[idx][0],
            footprintHeight: dimensions[idx][1],
            footprintOffsetX: 0,
            footprintOffsetY: 0
        }));

        const result = layout.solveLabelLayout(
            items, markers, {width: 799, height: 522}
        );
        const placements = Object.values(result);
        assert.strictEqual(placements.length, items.length);

        function boxGap(a, b) {
            const dx = Math.max(b.x - (a.x + a.width), a.x - (b.x + b.width), 0);
            const dy = Math.max(b.y - (a.y + a.height), a.y - (b.y + b.height), 0);
            return Math.hypot(dx, dy);
        }

        placements.forEach((placement, labelIdx) => {
            markers.forEach((marker, markerIdx) => {
                const spacing = markerIdx === labelIdx ? 0 : 1;
                assert(
                    !layout.boxesOverlap(placement.footprint, marker, spacing),
                    `La label ${labelIdx} copre il marker ${markerIdx}`
                );
            });
            assert(
                boxGap(placement.footprint, markers[labelIdx]) <= 1,
                `La label ${labelIdx} e' troppo staccata dal marker`
            );
            placements.slice(labelIdx + 1).forEach((other, offset) => {
                assert(
                    !layout.boxesOverlap(placement.footprint, other.footprint, 2),
                    `Collisione label ${labelIdx}-${labelIdx + offset + 1}`
                );
            });
        });
        """
    )

    subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_layout_stampa_vista_larga_sidi_resta_compatta():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js non disponibile")

    script = textwrap.dedent(
        """
        const assert = require('assert');
        const layout = require('./static/print_label_layout.js');

        // Coordinate in pixel della preview SIDI 09/06/2026 a zoom 15.25.
        const centers = [
            [749, 621], [593, 660], [753, 456], [694, 408], [713, 386],
            [713, 332], [690, 255], [663, 297], [621, 211], [550, 197],
            [559, 234], [561, 232], [614, 229], [578, 221], [735, 57],
            [704, 137], [712, 44], [737, 5], [746, 35], [748, -22],
            [640, 34]
        ];
        const dimensions = [
            [52.3, 28.5, 56, 35],
            [43.2, 28, 47, 35],
            [82.7, 46, 87, 52],
            ...Array(18).fill([43.2, 28, 47, 35])
        ];
        const markers = centers.map(([x, y], idx) => {
            const contentSize = idx === 2 ? 28 : 24;
            return {
                id: String(idx),
                x: x - contentSize / 2 - 1,
                y: y - contentSize / 2 - 1,
                width: contentSize + 8,
                height: contentSize + 8
            };
        });
        const items = centers.map(([x, y], idx) => ({
            id: String(idx),
            markerX: x,
            markerY: y,
            width: dimensions[idx][0],
            height: dimensions[idx][1],
            anchorWidth: dimensions[idx][0],
            anchorHeight: dimensions[idx][1],
            footprintWidth: dimensions[idx][2],
            footprintHeight: dimensions[idx][3],
            footprintOffsetX: 0,
            footprintOffsetY: 0
        }));

        const result = layout.solveLabelLayout(
            items, markers, { width: 1179, height: 640 }
        );
        const placements = Object.values(result);
        const averageDistance = placements.reduce(
            (total, placement) => total + placement.distance, 0
        ) / placements.length;

        assert.strictEqual(placements.length, centers.length);
        assert(
            Math.max(...placements.map(placement => placement.distance)) <= 72,
            'Una label supera il limite di emergenza di 72px'
        );
        assert(
            averageDistance < 26,
            `Distanza media troppo alta: ${averageDistance}px`
        );

        for (let i = 0; i < placements.length; i += 1) {
            markers.forEach((marker, markerIdx) => {
                const spacing = markerIdx === i ? 0 : 1;
                assert(
                    !layout.boxesOverlap(
                        placements[i].footprint, marker, spacing
                    ),
                    `La label ${i} copre il marker ${markerIdx}`
                );
            });
            for (let j = i + 1; j < placements.length; j += 1) {
                assert(
                    !layout.boxesOverlap(
                        placements[i].footprint, placements[j].footprint, 2
                    ),
                    `Sovrapposizione ${i}-${j}`
                );
                assert(
                    !layout.segmentsIntersect(
                        placements[i].line, placements[j].line
                    ),
                    `Incrocio ${i}-${j}`
                );
            }
        }
        """
    )

    subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
