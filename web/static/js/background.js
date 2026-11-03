/* ── Floating circles background ─────────────────────────── */
(function () {
    var canvas = document.getElementById('bg-canvas');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var circles = [];
    var animId = null;
    var dpr = window.devicePixelRatio || 1;

    /* Citrus palette with low opacity */
    var palette = [
        [255, 224, 51, 0.12],  // lemon
        [255, 140, 50, 0.10],  // orange
        [163, 217, 63, 0.09],  // lime
        [255, 180, 80, 0.08],  // soft orange
        [255, 230, 100, 0.07], // pale lemon
    ];

    function resize() {
        var w = window.innerWidth;
        var h = window.innerHeight;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        spawnCircles(w, h);
    }

    function spawnCircles(w, h) {
        circles = [];
        var count = Math.floor((w * h) / 80000); // ~1 circle per 80k px²
        count = Math.min(count, 20); // cap
        for (var i = 0; i < count; i++) {
            var r = 20 + Math.random() * 120;
            circles.push({
                x: Math.random() * w,
                y: Math.random() * h,
                r: r,
                dx: (Math.random() - 0.5) * 0.3,
                dy: (Math.random() - 0.5) * 0.3,
                color: palette[Math.floor(Math.random() * palette.length)]
            });
        }
    }

    function draw() {
        var w = window.innerWidth;
        var h = window.innerHeight;
        ctx.clearRect(0, 0, w, h);

        for (var i = 0; i < circles.length; i++) {
            var c = circles[i];
            ctx.beginPath();
            ctx.arc(c.x, c.y, c.r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(' + c.color[0] + ',' + c.color[1] + ',' + c.color[2] + ',' + c.color[3] + ')';
            ctx.fill();
        }
    }

    function loop() {
        draw();
        for (var i = 0; i < circles.length; i++) {
            var c = circles[i];
            c.x += c.dx;
            c.y += c.dy;
            var w = window.innerWidth;
            var h = window.innerHeight;
            if (c.x + c.r < 0) c.x = w + c.r;
            if (c.x - c.r > w) c.x = -c.r;
            if (c.y + c.r < 0) c.y = h + c.r;
            if (c.y - c.r > h) c.y = -c.r;
        }
        if (!prefersReduced) {
            animId = requestAnimationFrame(loop);
        }
    }

    window.addEventListener('resize', resize);

    // Pause when tab hidden
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            if (animId) cancelAnimationFrame(animId);
        } else if (!prefersReduced) {
            loop();
        }
    });

    resize();
    if (!prefersReduced) loop();
    else draw(); // static render once
})();
