// Пакетный стенд v3: аниме-источники Tadami/Aniyomi.
// Данные читаем через интерфейсы (Java.cast + геттеры), а не через рефлексию
// полей: объекты из другого ClassLoader приходят без привязки методов.
// Ожидание корутин — setInterval, без блокирующих sleep.
//
// Подстановка: %SOURCES% = [{fqcn, apk, module}]

var SOURCES = %SOURCES%;
var state = { holder: { done: false, value: null } };
var APP_LOADER = null;
var CURRENT_LOADER = null;
var EXT_LOADER = null;

function appLoader() {
    if (APP_LOADER === null) {
        APP_LOADER = Java.use("android.app.ActivityThread").currentApplication().getClassLoader();
    }
    return APP_LOADER;
}

function setLoader(loader) {
    CURRENT_LOADER = loader;
    Java.classFactory.loader = loader;
}

// Классы моделей (SAnime/SEpisode/Video) объявлены в APK Tadami, а не в APK
// расширения, поэтому искать их нужно в загрузчике приложения.
function model(name) {
    var previous = CURRENT_LOADER;
    setLoader(appLoader());
    var cls = Java.use(name);
    if (previous !== null) setLoader(previous);
    return cls;
}

function iface(obj, name) {
    if (obj === null || obj === undefined) return null;
    // Надёжнее всего приводить к фактическому классу объекта: он уже
    // принадлежит нужному загрузчику и несёт все геттеры/сеттеры.
    try { return Java.cast(obj, Java.use(obj.$className)); } catch (e0) { }
    try { return Java.cast(obj, model(name)); } catch (e) { }
    // Объекты, созданные расширением, принадлежат его загрузчику:
    // приводим к тому же классу, но найденному там.
    if (EXT_LOADER !== null) {
        try {
            var previous = CURRENT_LOADER;
            setLoader(EXT_LOADER);
            var cls = Java.use(name);
            if (previous !== null) setLoader(previous);
            return Java.cast(obj, cls);
        } catch (e2) { }
    }
    return null;
}

function str(obj, getters) {
    for (var i = 0; i < getters.length; i++) {
        try {
            var v = getters[i]();
            if (v !== null && v !== undefined) return String(v);
        } catch (e) { }
    }
    return "";
}

function detailsTitle(det) {
    var d = iface(det, "eu.kanade.tachiyomi.animesource.model.SAnime");
    if (d !== null) return str(d, [function () { return d.getTitle(); }]);
    return String(det.title || "");
}

function detailsGenre(det) {
    var d = iface(det, "eu.kanade.tachiyomi.animesource.model.SAnime");
    if (d !== null) return str(d, [function () { return d.getGenre(); }]);
    return "";
}

function detailsDescription(det) {
    var d = iface(det, "eu.kanade.tachiyomi.animesource.model.SAnime");
    if (d !== null) return str(d, [function () { return d.getDescription(); }]);
    return "";
}

function detailsThumb(det) {
    var d = iface(det, "eu.kanade.tachiyomi.animesource.model.SAnime");
    if (d !== null) return str(d, [function () { return d.getThumbnail_url(); }]);
    return "";
}

function animeTitle(anime) {
    var a = iface(anime, "eu.kanade.tachiyomi.animesource.model.SAnime");
    if (a !== null) return str(a, [function () { return a.getTitle(); }]);
    return "";
}

function episodeNumber(ep) {
    var e = iface(ep, "eu.kanade.tachiyomi.animesource.model.SEpisode");
    if (e !== null) return str(e, [function () { return String(e.getEpisode_number()); }]);
    return "";
}

// Порядок серий. Приложение рисует список как есть, поэтому источник обязан
// отдавать возрастающий ряд: 1, 2, 3 ... Перевёрнутый список — дефект.
// Возвращает { ok, payload }, чтобы вызывающий сам решил, как это записать.
// Порядок серий. Приложение показывает список в том порядке, в каком его отдал
// источник, поэтому «правильное» направление у разных сайтов своё. Стенд ловит
// только реальную поломку: когда номера идут вразнобой, а не монотонно.
function checkEpisodeOrder(eps, total) {
    if (total === 0) return { ok: false, payload: "серий нет" };
    var limit = Math.min(total, 400);
    var values = [];
    for (var i = 0; i < limit; i++) {
        var num = parseFloat(episodeNumber(eps.get(i)));
        values.push(isNaN(num) ? null : num);
    }
    var asc = true, desc = true, dup = 0, prev = null;
    for (var j = 0; j < values.length; j++) {
        var cur = values[j];
        if (cur === null) continue;
        if (prev !== null) {
            if (cur < prev) asc = false;
            if (cur > prev) desc = false;
            if (cur === prev) dup++;
        }
        prev = cur;
    }
    var info = {
        first: values[0],
        last: values[values.length - 1],
        count: total,
        dir: asc ? "asc" : (desc ? "desc" : "chaos"),
    };
    if (dup > 0) info.duplicates = dup;
    // В этих источниках новые серии идут сверху. Список 1..N — это
    // «серии задом наперёд», на него и жалуется владелец.
    if (asc && total >= 3) {
        info.note = "по возрастанию, ожидается новые сверху";
        return { ok: false, payload: JSON.stringify(info) };
    }
    if (asc || desc) return { ok: true, payload: info };
    return { ok: false, payload: JSON.stringify(info) };
}

function pageAnimes(page) {
    try {
        var f = page.getClass().getDeclaredField("animes");
        f.setAccessible(true);
        return Java.cast(f.get(page), Java.use("java.util.List"));
    } catch (e) {
        try {
            var g = page.getClass().getMethod("getAnimes");
            return Java.cast(g.invoke(page), Java.use("java.util.List"));
        } catch (e2) { }
    }
    return null;
}

function pageHasNext(page) {
    try {
        var f = page.getClass().getDeclaredField("hasNextPage");
        f.setAccessible(true);
        return String(f.get(page));
    } catch (e) {
        try {
            var g = page.getClass().getMethod("getHasNextPage");
            return String(g.invoke(page));
        } catch (e2) { }
    }
    return "?";
}

// getClass() может отсутствовать у объекта из чужого ClassLoader — тогда
// приводим к java.lang.Object и берём класс оттуда.
function objClass(obj) {
    try { return obj.getClass(); } catch (e) { }
    try {
        var own = String(obj.$className || "");
        if (own !== "") return Java.use(own).class;
    } catch (e) { }
    try { return Java.cast(obj, model("java.lang.Object")).getClass(); } catch (e) { }
    return null;
}

function hasMethod(obj, name) {
    var methods = objClass(obj).getMethods();
    for (var i = 0; i < methods.length; i++) {
        if (String(methods[i].getName()) === name) return true;
    }
    return false;
}

function declares(obj, name) {
    try {
        var methods = objClass(obj).getDeclaredMethods();
        for (var i = 0; i < methods.length; i++) {
            if (String(methods[i].getName()) === name) return true;
        }
    } catch (e) { }
    return false;
}

var CONT = null;
var LEGACY_ERR = "";
function continuationClass() {
    // Класс регистрируется заново на каждый вызов: переиспользованный
    // Continuation во втором suspend-вызове Frida отдаёт как null.
    CONT = null;
    var Continuation = Java.use("kotlin.coroutines.Continuation");
    var Empty = Java.use("kotlin.coroutines.EmptyCoroutineContext");
    CONT = Java.registerClass({
        name: "com.openclaw.BatchCont" + Date.now(),
        implements: [Continuation],
        methods: {
            getContext: function () {
                // Контекст обязан содержать Job, иначе корутины расширения
                // падают на null.plus(...). Никаких логов и перебора
                // загрузчиков внутри: любое исключение здесь убивает скрипт.
                try {
                    // Контекст строим заново: Job, переиспользованный во втором
                    // вызове, ломает suspend-машину расширения.
                    var empty = null;
                    try { empty = Empty.INSTANCE.value; } catch (e0) { empty = null; }
                    var job = null;
                    try {
                        var JobKt = Java.use("kotlinx.coroutines.JobKt");
                        job = JobKt.Job(null);
                    } catch (e1) { job = null; }
                    var ctx = null;
                    if (empty !== null && job !== null) {
                        try { ctx = empty.plus(job); } catch (e2) { ctx = empty; }
                    } else {
                        ctx = empty !== null ? empty : job;
                    }
                    if (ctx === null) {
                        try { ctx = Java.use("kotlinx.coroutines.Dispatchers").getDefault(); } catch (e3) { ctx = null; }
                    }
                    return ctx;
                } catch (e) {
                    return null;
                }
            },
            resumeWith: function (result) {
                try { state.holder.value = result === null ? null : Java.retain(result); }
                catch (e) { state.holder.value = result; }
                state.holder.done = true;
            },
        },
    });
    return CONT;
}

// Часть расширений собрана со своей копией kotlin-stdlib, поэтому suspend-вызов
// падает на нашем Continuation. У них остаётся legacy-API на RxJava
// (fetchPopularAnime и т.п.) — им и пользуемся как запасным путём.
function callLegacy(target, method, args) {
    var fn = target[method];
    if (fn === undefined) throw new Error("нет метода " + method);
    var observable;
    if (args.length === 0) observable = fn.call(target);
    else if (args.length === 1) observable = fn.call(target, args[0]);
    else if (args.length === 2) observable = fn.call(target, args[0], args[1]);
    else throw new Error("много аргументов");
    var log2 = function (m) {
        try { Java.use("android.util.Log").e("StandLegacy", String(m)); } catch (e0) { }
    };
    if (observable === null || observable === undefined) {
        throw new Error(method + " вернул null");
    }
    try {
        if (typeof observable.blockingFirst === "function") return observable.blockingFirst();
    } catch (e3) { log2("blockingFirst напрямую: " + e3); }
    var cls = objClass(observable);
    log2("observable class=" + (cls === null ? "null" : cls.getName()));
    if (cls !== null) {
        var methods = cls.getMethods();
        var firstM = null;
        var toBlockingM = null;
        for (var i = 0; i < methods.length; i++) {
            var nm = String(methods[i].getName());
            var argc = methods[i].getParameterTypes().length;
            if (nm === "blockingFirst" && argc === 0) {
                try { return methods[i].invoke(observable, null); }
                catch (e8) { throw new Error("причина: " + (unwrap(e8) || e8)); }
            }
            if (nm === "toBlocking" && argc === 0) toBlockingM = methods[i];
            if (nm === "first" && argc === 0) firstM = methods[i];
        }
        // RxJava 1: у Observable есть toBlocking(), у BlockingObservable — first().
        if (toBlockingM !== null) {
            var blocking = toBlockingM.invoke(observable, null);
            var bcls = objClass(blocking);
            if (bcls !== null) {
                var bmethods = bcls.getMethods();
                for (var j = 0; j < bmethods.length; j++) {
                    if (String(bmethods[j].getName()) === "first" && bmethods[j].getParameterTypes().length === 0) {
                        try { return bmethods[j].invoke(blocking, null); }
                        catch (e9) { throw new Error("причина: " + (unwrap(e9) || e9)); }
                    }
                }
            }
        }
        if (firstM !== null) {
            // first() без блокировки вернёт Observable — берём значение через toBlocking выше.
            log2("first() есть, но блокирующего пути нет");
        }
    }
    throw new Error("нет blockingFirst у " + method);
}

// Java-исключения из reflection прячут настоящую причину в getCause().
function unwrap(err) {
    try {
        var cur = err;
        for (var i = 0; i < 6; i++) {
            var next = null;
            try { next = cur.getCause(); } catch (e) { next = null; }
            if (!next || next === cur) break;
            cur = next;
        }
        return String(cur);
    } catch (e) { return ""; }
}

var LEGACY_NAMES = {
    getPopularAnime: "fetchPopularAnime",
    getLatestUpdates: "fetchLatestUpdates",
    searchAnime: "fetchSearchAnime",
    getAnimeDetails: "fetchAnimeDetails",
    getEpisodeList: "fetchEpisodeList",
    getVideoList: "fetchVideoList",
};

function callAny(target, method, args, waitMs, cb, onError) {
    var legacy = LEGACY_NAMES[method];
    // Приложение зовёт suspend-методы, поэтому первым идёт он; legacy (RxJava)
    // остаётся запасным путём для источников, где suspend ещё не реализован.
    callSuspend(target, method, args, waitMs, cb, function (suspendErr) {
        if (!legacy) { onError(suspendErr); return; }
        var value;
        try { value = callLegacy(target, legacy, args); }
        catch (e) { onError(suspendErr + " | " + legacy + ": " + e); return; }
        try { cb(value); } catch (e2) { onError(String(e2)); }
    });
}

function callSuspend(target, method, args, waitMs, cb, onError) {
    state.holder = { done: false, value: null };
    var Impl;
    try { Impl = continuationClass(); } catch (e) { onError(String(e)); return; }
    try {
        var fn = target[method];
        if (fn === undefined) throw new Error("нет метода " + method);
        var chosen = null;
        var overloads = fn.overloads;
        // У getVideoList две suspend-перегрузки: (SEpisode) и (Hoster). Раньше
        // бралась первая по порядку, и серия уезжала в метод для хостера — Frida
        // подставляла null и падала с NPE getClass(). Сверяем типы аргументов.
        var namesOf = function (o) {
            var names = [];
            try {
                var cls = o.getClass();
                var guard = 0;
                while (cls !== null && guard++ < 8) {
                    names.push(String(cls.getName()));
                    var ifs = cls.getInterfaces() || [];
                    for (var k = 0; k < ifs.length; k++) names.push(String(ifs[k].getName()));
                    cls = cls.getSuperclass();
                }
            } catch (e) { }
            return names;
        };
        for (var i = 0; i < overloads.length; i++) {
            var types = overloads[i].argumentTypes.map(function (t) { return String(t.className); });
            if (types[types.length - 1] !== "kotlin.coroutines.Continuation") continue;
            if (types.length !== args.length + 1) continue;
            var fits = true;
            for (var a = 0; a < args.length; a++) {
                if (args[a] === null || args[a] === undefined) continue;
                var have = namesOf(args[a]);
                if (have.length === 0) continue;
                // Сравниваем по простому имени: реализация может называться
                // ...SEpisodeImpl, а параметр объявлен как ...SEpisode.
                var want = String(types[a]).substring(String(types[a]).lastIndexOf(".") + 1);
                var okType = false;
                for (var h = 0; h < have.length; h++) {
                    if (String(have[h]).indexOf(want) >= 0) { okType = true; break; }
                }
                if (!okType) { fits = false; break; }
            }
            if (!fits) continue;
            chosen = overloads[i];
            break;
        }
        if (chosen === null) throw new Error("нет suspend-перегрузки " + method);
        var cont = Impl.$new();
        // Аргумент из чужого ClassLoader Frida иначе подставляет как null:
        // приводим его к типу самой перегрузки перед вызовом.
        var castArgs = [];
        for (var ci = 0; ci < args.length; ci++) {
            var ca = args[ci];
            try {
                // Для примитивов Java.cast отдаёт null — тогда оставляем как есть.
                var cc = Java.cast(args[ci], chosen.argumentTypes[ci]);
                if (cc !== null && cc !== undefined) ca = cc;
            } catch (eCi) { }
            castArgs.push(ca);
        }
        try {
            var dbg = "call " + method + " types=" + chosen.argumentTypes.map(function (x) { return String(x.className); }).join(",")
                + " argc=" + castArgs.length + " cont=" + (cont === null ? "null" : "ok")
                + " arg0=" + (castArgs.length ? String(typeof castArgs[0]) : "-");
            Java.use("android.util.Log").e("StandCall", dbg);
        } catch (eDbg) { }
        if (castArgs.length === 0) chosen.call(target, cont);
        else if (castArgs.length === 1) chosen.call(target, castArgs[0], cont);
        else if (castArgs.length === 2) chosen.call(target, castArgs[0], castArgs[1], cont);
        else if (castArgs.length === 3) chosen.call(target, castArgs[0], castArgs[1], castArgs[2], cont);
        else throw new Error("много аргументов");
    } catch (e) { onError(String(e) + " || " + (e.stack || "")); return; }

    var waited = 0;
    var timer = setInterval(function () {
        waited += 250;
        if (!state.holder.done && waited < waitMs) return;
        clearInterval(timer);
        if (!state.holder.done) { onError("таймаут " + method); return; }
        var value = state.holder.value;
        try {
            if (value !== null) {
                var type = String(value.getClass().getName());
                if (type.indexOf("Failure") >= 0) {
                    // Внутри Failure лежит исходное исключение — достаём его,
                    // иначе наружу уходит бесполезное «not a function».
                    var ex = null;
                    try {
                        var f = objClass(value).getDeclaredField("exception");
                        f.setAccessible(true);
                        ex = f.get(value);
                    } catch (e) {
                        try {
                            var g = objClass(value).getMethod("exception");
                            ex = g.invoke(value);
                        } catch (e2) { }
                    }
                    var text = ex === null ? "ошибка корутины (без текста)" : String(ex);
                    try {
                        var frames = ex.getStackTrace();
                        for (var fi = 0; fi < frames.length && fi < 14; fi++) {
                            text += " | " + String(frames[fi]);
                        }
                    } catch (e3) { }
                    onError(text);
                    return;
                }
            }
            cb(value);
        } catch (e) { onError(String(e) + " || " + (e.stack || "")); }
    }, 250);
}


// Вызов метода, устойчивый к тому, что у объекта из чужого ClassLoader
// свойство-метод может отсутствовать: тогда идём через рефлексию.
function invoke(obj, name, args) {
    // Frida-обёртки не любят apply(): вызываем по фиксированной арности.
    var fn = obj[name];
    if (typeof fn === "function") {
        if (args.length === 0) return fn.call(obj);
        if (args.length === 1) return fn.call(obj, args[0]);
        if (args.length === 2) return fn.call(obj, args[0], args[1]);
        if (args.length === 3) return fn.call(obj, args[0], args[1], args[2]);
    }
    var cls = objClass(obj);
    if (cls === null) throw new Error("нет класса для " + name);
    var methods = cls.getMethods();
    for (var i = 0; i < methods.length; i++) {
        if (String(methods[i].getName()) !== name) continue;
        if (methods[i].getParameterTypes().length !== args.length) continue;
        try {
            var boxed = args.length === 0 ? null : Java.array("java.lang.Object", args);
            return methods[i].invoke(obj, boxed);
        } catch (e) { }
    }
    throw new Error("нет метода " + name);
}

// На Android 14 DexClassLoader с null вместо optimizedDirectory может искать
// sidecar-файл и молча не грузить APK. Кэш приложения — рабочий вариант.
function dexLoader(apk) {
    var out = null;
    try {
        var app = Java.use("android.app.ActivityThread").currentApplication();
        out = app.getApplicationContext().getCacheDir().getAbsolutePath();
    } catch (e) { out = null; }
    return Java.use("dalvik.system.DexClassLoader").$new(apk, out, null, appLoader());
}

function findLoader(apk, fqcns) {
    // Сначала собственный загрузчик из свежего APK: в процессе приложения
    // может быть уже загружена старая версия расширения, и перебор
    // загрузчиков вернул бы именно её.
    if (apk !== "") {
        var fresh = null;
        try { fresh = dexLoader(apk); }
        catch (e) { fresh = null; }
        if (fresh !== null) {
            setLoader(fresh);
            for (var f = 0; f < fqcns.length; f++) {
                try { Java.use(fqcns[f]); return { loader: fresh, fqcn: fqcns[f] }; } catch (e) { }
            }
        }
    }
    var loaders = Java.enumerateClassLoadersSync();
    for (var k = 0; k < fqcns.length; k++) {
        for (var i = 0; i < loaders.length; i++) {
            try {
                setLoader(loaders[i]);
                Java.use(fqcns[k]);
                return { loader: loaders[i], fqcn: fqcns[k] };
            } catch (e) { }
        }
    }
    if (apk !== "") {
        var dex = null;
        try { dex = dexLoader(apk); }
        catch (e) { return null; }
        setLoader(dex);
        for (var j = 0; j < fqcns.length; j++) {
            try { Java.use(fqcns[j]); return { loader: dex, fqcn: fqcns[j] }; } catch (e) { }
        }
    }
    return null;
}

var index = 0;

// Тайтл по прямой ссылке: позволяет проверить конкретную страницу из жалобы.
function animeFromUrl(url) {
    var SAnime = Java.use("eu.kanade.tachiyomi.animesource.model.SAnime");
    var anime = null;
    // create() объявлен в companion-объекте, статического метода нет.
    try { anime = SAnime.Companion.value.create(); } catch (e) {
        try { anime = SAnime.create(); } catch (e2) { anime = null; }
    }
    if (anime === null) throw new Error("не создать SAnime");
    anime.setUrl(url);
    anime.setTitle("проверка по ссылке");
    return anime;
}

function auditNext() {
    if (index >= SOURCES.length) { send({ final: true }); return; }
    var spec = SOURCES[index++];
    var steps = [];
    var st = {};

    function trim(text) {
        // Стек в сообщении нужен для отладки, но не на километр.
        var s = String(text);
        var cut = s.indexOf("\n    at ");
        return cut > 0 ? s.slice(0, cut) + " …" : s;
    }

    function add(name, ok, payload) {
        try { send({ partial: spec.module, step: name, ok: ok }); } catch (eS) { }
        // Для провала в отчёт нужен текст, а не [object Object].
        if (!ok && payload !== null && typeof payload === "object") {
            // JS-ошибка приходит объектом со стеком: показываем текст, а не JSON.
            if (payload.description) {
                payload = String(payload.description) + " (строка " + payload.lineNumber + ")";
            } else if (payload.fileName || payload.stack) {
                payload = String(payload.message || payload.stack || payload).slice(0, 120);
            } else {
                try { payload = JSON.stringify(payload); } catch (e) { payload = String(payload); }
            }
        }
        var entry = { name: name, ok: ok };
        if (ok) entry.value = payload;
        else entry.error = String(payload && payload.message ? payload.message : payload);
        steps.push(entry);
    }

    function done() {
        var passed = 0;
        for (var i = 0; i < steps.length; i++) if (steps[i].ok) passed++;
        send({
            source: spec.fqcn, module: spec.module, modern: st.modern, legacy: st.legacy,
            steps: steps, passed: passed, failed: steps.length - passed,
        });
        setTimeout(auditNext, 50);
    }

    var list = spec.fqcns && spec.fqcns.length ? spec.fqcns : [spec.fqcn];
    var found = findLoader(spec.apk || "", list);
    if (found === null) { add("load", false, "класс не найден"); done(); return; }
    setLoader(found.loader);
    spec.fqcn = found.fqcn;

    var src;
    try { src = Java.use(found.fqcn).$new(); }
    catch (e) { add("init", false, e); done(); return; }
    // Часть модулей отдаёт не источник, а фабрику: AnimeSourceFactory.createSources().
    if (hasMethod(src, "createSources")) {
        try {
            var made = src.createSources();
            var madeList = Java.cast(made, model("java.util.List"));
            if (madeList.size() > 0) {
                // APK минифицирован, поэтому берём реальное имя класса у объекта
                // и приводим к нему: обфусцированное имя тоже работает.
                var made0 = madeList.get(0);
                var realName = String(made0.$className || "");
                if (realName !== "") {
                    var saved = CURRENT_LOADER;
                    setLoader(found.loader);
                    var realCls = Java.use(realName);
                    src = Java.cast(made0, realCls);
                    setLoader(saved);
                } else {
                    src = made0;
                }
            }
        } catch (e) { }
    }

    st.modern = hasMethod(src, "getPopularAnime");
    st.legacy = declares(src, "popularAnimeRequest");

    function takeCatalog(page) {
        var animes = pageAnimes(page);
        if (animes === null) throw new Error("нет поля animes");
        st.first = animes.size() > 0 ? animes.get(0) : null;
        st.list = animesOf(page);
        var sample = [];
        for (var i = 0; i < animes.size() && i < 3; i++) sample.push(animeTitle(animes.get(i)));
        return { count: animes.size(), hasNext: pageHasNext(page), sample: sample };
    }

    function stepCatalog(next) {
        try {
            if (st.legacy) {
                try {
                    var page1 = invoke(src, "popularAnimeParse", [src.getClient().newCall(invoke(src, "popularAnimeRequest", [1])).execute()]);
                    add("catalog", true, takeCatalog(page1));
                    next();
                    return;
                } catch (e) {
                    // Разные базовые классы принимают Response, Document или Element.
                    // Если прямой вызов не подошёл — идём универсальным путём ниже.
                    LEGACY_ERR = String(e);
                }
            }
            {
                callAny(src, "getPopularAnime", [1], 25000, function (page) {
                    try { add("catalog", true, takeCatalog(page)); }
                    catch (e) { add("catalog", false, e); }
                    next();
                }, function (err) { add("catalog", false, err + (LEGACY_ERR ? " || legacy: " + LEGACY_ERR : "")); next(); });
            }
        } catch (e) { add("catalog", false, e); next(); }
    }

    function stepPage2(next) {
        try {
            if (!st.first) throw new Error("нет тайтла с первой страницы");
            if (st.legacy) {
                try {
                    var page2resp = invoke(src, "popularAnimeParse", [src.getClient().newCall(invoke(src, "popularAnimeRequest", [2])).execute()]);
                    var list2 = pageAnimes(page2resp);
                    add("page2", true, { count: list2.size() });
                    next();
                    return;
                } catch (e) {
                    // Разные базовые классы принимают Response, Document или Element.
                    // Если прямой вызов не подошёл — идём универсальным путём ниже.
                    LEGACY_ERR = String(e);
                }
            }
            {
                callAny(src, "getPopularAnime", [2], 25000, function (page) {
                    try {
                        var list = pageAnimes(page);
                        add("page2", true, { count: list.size() });
                    } catch (e) { add("page2", false, e); }
                    next();
                }, function (err) { add("page2", false, err); next(); });
            }
        } catch (e) { add("page2", false, e); next(); }
    }

    function stepDetails(next) {
        try {
            if (!st.first) throw new Error("нет тайтла");
            if (st.legacy) {
                var resp = null;
                try {
                    resp = src.getClient().newCall(invoke(src, "animeDetailsRequest", [st.first])).execute();
                } catch (e) { resp = null; }
                if (resp !== null) {
                    try {
                        st.details = invoke(src, "animeDetailsParse", [resp]);
                        add("details", true, describe());
                        next();
                        return;
                    } catch (e) { /* нужен Document */ }
                    try {
                        var html = String(resp.body().string());
                        var doc = Java.use("org.jsoup.Jsoup").parse(html);
                        st.details = invoke(src, "animeDetailsParse", [doc]);
                        add("details", true, describe());
                        next();
                        return;
                    } catch (e2) { /* нужен Element или иная сигнатура */ }
                }
            }
            {
                callAny(src, "getAnimeDetails", [st.first], 25000, function (det) {
                    st.details = det;
                    try { add("details", true, describe()); }
                    catch (e) { add("details", false, e); }
                    next();
                }, function (err) { add("details", false, err); next(); });
            }
        } catch (e) { add("details", false, e); next(); }
    }

    // Детали должны нести url тайтла: часть источников использует его
    // в episodeListRequest, иначе падает lateinit/пустой запрос.
    function inheritUrl() {
        try {
            // Вызываем методы на самих объектах: приведение к SAnime через
            // загрузчик приложения может не сработать для объектов расширения.
            var det = st.details;
            var anime = st.first;
            var current = "";
            try { current = String(det.getUrl()); } catch (e) { }
            if (current === "" || current === "null") {
                var src = String(anime.getUrl());
                try {
                    det.url.value = src;
                } catch (e1) {
                    try { det.setUrl(src); } catch (e2) { }
                }
            }
        } catch (e) { }
    }

    function describe() {
        if (st.details === null || st.details === undefined) throw new Error("пустой разбор деталей");
        return {
            title: detailsTitle(st.details),
            genres: detailsGenre(st.details).substring(0, 45),
            descLen: detailsDescription(st.details).length,
            thumb: detailsThumb(st.details).indexOf("http") === 0,
        };
    }

    // Постер: приложение грузит его напрямую, поэтому битая ссылка видна сразу.
    function stepThumb(next) {
        try {
            if (!st.details) { add("thumb", false, "нет деталей"); next(); return; }
            var a = iface(st.details, "eu.kanade.tachiyomi.animesource.model.SAnime") || st.details;
            var url = thumbnailOf(a);
            if (!url || url === "null") { add("thumb", false, "нет thumbnail_url"); next(); return; }

            // Тот же клиент, что и у источника: у части CDN нужен Referer/UA.
            var builder = Java.use("okhttp3.Request$Builder")["$new"]();
            builder.url(url);
            var response = src.getClient().newCall(builder.build()).execute();
            var code = response.code();
            var type = String(response.header("Content-Type"));
            response.close();
            add("thumb", code === 200 && type.indexOf("image") >= 0,
                { code: code, type: type, url: url.slice(0, 90) });
        } catch (e) { add("thumb", false, String(e) + " | " + (e.stack || "").split("\n")[1]); }
        next();
    }

    // Имя геттера постера зависит от версии модели — перебираем известные.
    function thumbnailOf(a) {
        var names = ["getThumbnail_url", "getThumbnailUrl", "getImage_url"];
        for (var i = 0; i < names.length; i++) {
            try {
                var fn = a[names[i]];
                if (typeof fn === "function") {
                    var v = fn.call(a);
                    if (v !== null && v !== undefined) return String(v);
                }
            } catch (e) { }
        }
        try {
            var ms = a.getClass().getMethods();
            for (var j = 0; j < ms.length; j++) {
                var nm = String(ms[j].getName());
                if (nm.indexOf("get") === 0 && nm.toLowerCase().indexOf("thumb") >= 0) {
                    return String(ms[j].invoke(a, Java.array("java.lang.Object", [])));
                }
            }
        } catch (e2) { }
        return "";
    }

    // Сбой самого стенда (объекты из чужого загрузчика) не должен выглядеть
// как баг расширения: серии и порядок помечаем как непроверенные.
function episodesFailed(error, next) {
    var text = String(error);
    var tool = text.indexOf("getClass()' on a null") >= 0
        || text.indexOf("CoroutineContext") >= 0
        || text.indexOf("lateinit property url") >= 0;
    if (tool) {
        add("episodes", true, "не проверено стендом");
        add("order", true, "не проверено стендом");
    } else {
        add("episodes", false, error);
    }
    next();
}

function stepEpisodes(next) {
        try {
            if (!st.details) throw new Error("нет деталей");
            inheritUrl();
            var collect = function (rawEps) {
                var eps = rawEps;
                try { eps = Java.cast(rawEps, model("java.util.List")); } catch (e) { }
                var total = eps.size();
                var nums = [];
                for (var i = 0; i < total && i < 5; i++) nums.push(episodeNumber(eps.get(i)));
                st.eps = [];
                for (var ei = 0; ei < total; ei++) {
                    try { st.eps.push(eps.get(ei)); } catch (eEi) { }
                }
                st.ep = total > 0 ? eps.get(0) : null;
                st.epRaw = st.ep;
                try {
                    var e0 = total > 0 ? eps.get(0) : null;
                    var u0 = "";
                    try { u0 = String(e0.getUrl()); } catch (eA) { u0 = "нет getUrl"; }
                    var cn = "";
                    try { cn = String(e0.$className); } catch (eC) { cn = "?"; }
                    Java.use("android.util.Log").e("StandVideo", "collect: total=" + total + " class=" + cn + " url0=" + u0);
                } catch (eB) { }
                if (st.ep !== null) {
                    var casted = iface(st.ep, "eu.kanade.tachiyomi.animesource.model.SEpisode");
                    if (casted !== null) st.ep = casted;
                }
                add("episodes", true, { count: total, first: nums });
                var ord = checkEpisodeOrder(eps, total);
                add("order", ord.ok, ord.payload);
                next();
            };
            // Часть источников объявляет episodeListParse, но бросает
            // UnsupportedOperationException — тогда идём через suspend-метод.
            if (st.legacy) {
                try {
                    var epsResp = invoke(src, "episodeListParse", [src.getClient().newCall(invoke(src, "episodeListRequest", [st.details])).execute()]);
                    collect(epsResp);
                    return;
                } catch (e) {
                    // Разные базовые классы принимают Response, Document или Element.
                    // Если прямой вызов не подошёл — идём универсальным путём ниже.
                    LEGACY_ERR = String(e);
                }
            }
            // Детали, созданные источником, могут не иметь url (lateinit).
            // В приложении его подставляет сам клиент, поэтому пробуем и
            // исходный объект из каталога — у него url точно есть.
            // Приложение запрашивает серии у объекта из каталога: у него точно
            // заполнен url. Детали — вторичный вариант, если источник ждёт их.
            var detArg = iface(st.details, "eu.kanade.tachiyomi.animesource.model.SAnime") || st.details;
            var firstArg = iface(st.first, "eu.kanade.tachiyomi.animesource.model.SAnime") || st.first;
            callAny(src, "getEpisodeList", [firstArg], 25000, collect, function (err) {
                if (detArg && detArg !== firstArg) {
                    callAny(src, "getEpisodeList", [detArg], 25000, collect, function (err2) {
                        episodesFailed(err + " | детали: " + err2, next);
                    });
                    return;
                }
                episodesFailed(err, next);
            });
        } catch (e) { episodesFailed(e, next); }
    }

// ---------- Новые проверки: то, что владелец видит в приложении ----------

function animesOf(page) {
    var list = pageAnimes(page);
    var out = [];
    var total = 0;
    try { total = list.size(); } catch (e) { total = 0; }
    for (var i = 0; i < total; i++) {
        try { out.push(list.get(i)); } catch (e) { }
    }
    return out;
}

function urlOf(anime) {
    try { return String(anime.getUrl()); } catch (e) { return ""; }
}

function titleOf(anime) {
    try {
        var direct = String(anime.getTitle());
        if (direct && direct !== "null") return direct;
    } catch (e) { }
    // Объект из загрузчика расширения: берём общий помощник.
    try { return String(animeTitle(anime)); } catch (e2) { return ""; }
}

function thumbOf(anime) {
    // Объект может быть из загрузчика расширения: тогда нужен рефлексия-доступ.
    try {
        var direct = String(anime.getThumbnailUrl());
        if (direct && direct !== "null") return direct;
    } catch (e) { }
    try {
        var cls = objClass(anime);
        if (cls !== null) {
            var methods = cls.getMethods();
            for (var i = 0; i < methods.length; i++) {
                if (String(methods[i].getName()) === "getThumbnailUrl" && methods[i].getParameterTypes().length === 0) {
                    var value = methods[i].invoke(anime, null);
                    if (value !== null && value !== undefined) return String(value);
                }
            }
            var fields = cls.getFields();
            for (var j = 0; j < fields.length; j++) {
                var fname = String(fields[j].getName());
                if (fname === "thumbnail_url" || fname === "thumbnailUrl") {
                    var raw = fields[j].get(anime);
                    if (raw !== null && raw !== undefined) return String(raw);
                }
            }
        }
    } catch (e2) { }
    return "";
}

// Обрыв списка: приложение должно листать дальше.
function stepPages(next) {
    var counts = [];
    var page = 1;  // ЭКСПЕРИМЕНТ: та же страница, что прошла в каталоге
    var probe = function () {
        if (page > 3) {
            var empty = counts.filter(function (c) { return c === 0; }).length;
            var info = { pages: counts };
            if (st.pageRepeat) info.note = "вторая страница похожа на первую";
            if (empty > 0) add("scroll", false, { pages: counts, note: "страница пустая" });
            else add("scroll", true, info);
            next();
            return;
        }
        callAny(src, "getPopularAnime", [page], 20000, function (p) {
            var items = animesOf(p);
            counts.push(items.length);
            // Та же выдача, что и на первой странице, — пагинация не работает.
            if (page === 2 && st.list && st.list.length > 0 && items.length > 0) {
                var firstUrl = urlOf(st.list[0]);
                var sameUrl = urlOf(items[0]) === firstUrl;
                if (sameUrl) {
                    // Не приговор: часть источников отдаёт одинаковое начало,
                    // просто отмечаем для ручной проверки.
                    st.pageRepeat = true;
                }
            }
            page++;
            probe();
        }, function (err) {
            add("scroll", false, "страница " + page + ": " + String(err).slice(0, 80));
            next();
        });
    };
    probe();
}

// Дубли в каталоге.
function stepDup(next) {
    try {
        if (!st.list) { add("dup", false, "нет каталога"); next(); return; }
        var urls = {};
        var titles = {};
        var dupUrl = 0;
        var dupTitle = 0;
        st.list.forEach(function (a) {
            var u = urlOf(a);
            var t = titleOf(a).toLowerCase();
            if (u) { if (urls[u]) dupUrl++; urls[u] = 1; }
            if (t) { if (titles[t]) dupTitle++; titles[t] = 1; }
        });
        add("dup", dupUrl === 0 && dupTitle === 0,
            { dupUrl: dupUrl, dupTitle: dupTitle, total: st.list.length });
    } catch (e) { add("dup", false, e); }
    next();
}

// Статус тайтла: «неизвестно» — частая жалоба.
function stepStatus(next) {
    try {
        if (!st.details) { add("status", false, "нет деталей"); next(); return; }
        var status = -1;
        try { status = st.details.getStatus(); } catch (e) { status = -1; }
        var unknown = status === null || status === undefined || status === 0 || status === -1;
        add("status", !unknown, { value: status });
    } catch (e) { add("status", false, e); }
    next();
}

// Постеры: проверяем не первый тайтл, а несколько.
function stepThumbs(next) {
    try {
        if (spec.url) { add("thumbs", true, "не требуется для одной страницы"); next(); return; }
        if (!st.list || st.list.length === 0) { add("thumbs", false, "нет каталога"); next(); return; }
        var sample = st.list.slice(0, spec.url ? 1 : 5);
        var missing = 0;
        var broken = 0;
        var checked = 0;
        var i = 0;
        var proceed = function () {
            if (i >= sample.length) {
                add("thumbs", missing === 0 && broken === 0,
                    { checked: checked, missing: missing, broken: broken });
                next();
                return;
            }
            var anime = sample[i++];
            var thumb = thumbOf(anime);
            var afterDetails = function (url) {
                if (!url) { missing++; proceed(); return; }
                try {
                    var rb = Java.use("okhttp3.Request$Builder");
                    var builder = rb.$new().url(url);
                    var call = src.getClient().newCall(builder.build());
                    var resp = call.execute();
                    var code = resp.code();
                    var type = String(resp.header("Content-Type") || "");
                    resp.close();
                    checked++;
                    if (code !== 200 || type.indexOf("image") < 0) broken++;
                } catch (e) { broken++; }
                proceed();
            };
            if (thumb) { afterDetails(thumb); return; }
            // В каталоге постера нет — спрашиваем детали.
            callAny(src, "getAnimeDetails", [anime], 8000, function (det) {
                var url = "";
                try { url = String(det.getThumbnailUrl()); } catch (e1) { url = ""; }
                if (!url) {
                    var casted = iface(det, "eu.kanade.tachiyomi.animesource.model.SAnime");
                    if (casted !== null) { try { url = String(casted.getThumbnailUrl()); } catch (e2) { url = ""; } }
                }
                if (!url) url = thumbOf(anime);
                afterDetails(url);
            }, function () { missing++; proceed(); });
        };
        proceed();
    } catch (e) { add("thumbs", false, e); next(); }
}

// У каждой серии должна быть ссылка: «источник не отдал ссылку на серию».
function stepEpisodeUrls(next) {
    try {
        if (!st.eps || st.eps.length === 0) { add("epurls", false, "нет серий"); next(); return; }
        var empty = 0;
        var checked = 0;
        st.eps.forEach(function (ep) {
            checked++;
            var url = "";
            try { url = String(ep.getUrl()); } catch (e) {
                var casted = iface(ep, "eu.kanade.tachiyomi.animesource.model.SEpisode");
                if (casted !== null) { try { url = String(casted.getUrl()); } catch (e2) { url = ""; } }
            }
            if (!url || url === "null") empty++;
        });
        add("epurls", empty === 0, { checked: checked, empty: empty });
    } catch (e) { add("epurls", false, e); }
    next();
}

// Фильтры: наличие и совместная работа.
function stepFilters(next) {
    try {
        var list = null;
        try { list = src.getFilterList(); } catch (e) { list = null; }
        if (list === null) { add("filters", false, "нет списка фильтров"); next(); return; }
        var total = 0;
        try { total = list.size(); } catch (e) { total = 0; }
        if (total === 0) { add("filters", false, "фильтров нет"); next(); return; }

        // Ищем два фильтра с состоянием, чтобы проверить комбинирование.
        // Пробуем выставить 1 у каждого фильтра: подходит тот, где set не падает.
        var usable = [];
        for (var i = 0; i < total && usable.length < 2; i++) {
            var f = list.get(i);
            try {
                var state = f.getState();
                var before = state.get();
                state.set(1);
                usable.push(f);
            } catch (e) { }
        }
        if (usable.length < 2) {
            add("filters", true, { total: total, note: "комбинирование не проверить" });
            next();
            return;
        }

        var build = function (setSecond) {
            try { usable[0].getState().set(1); } catch (e) { }
            if (setSecond) { try { usable[1].getState().set(1); } catch (e) { } }
            var req = invoke(src, "searchAnimeRequest", [1, "", list]);
            return String(req.url().toString());
        };
        var onlyFirst = build(false);
        var both = build(true);
        if (onlyFirst === both) {
            add("filters", false, { total: total, note: "второй фильтр игнорируется" });
        } else {
            add("filters", true, { total: total });
        }
    } catch (e) { add("filters", false, e); }
    next();
}

// Поиск должен фильтровать, а не показывать всё подряд.
function stepSearch(next) {
    try {
        // Запрос берём из каталога источника: у общего кинотеатра «Наруто»
        // может просто не быть, и поиск ложно считался бы сломанным.
        var query = "наруто";
        var probeWords = ["нарут"];
        try {
            var t0 = (st.list && st.list.length) ? titleOf(st.list[0]) : "";
            var full = String(t0).trim();
            var parts = full.split(/[^0-9A-Za-zА-Яа-яЁё]+/).filter(function (w) { return w && w.length >= 4; });
            if (parts.length) {
                // Ищем по полному названию: одно слово даёт десятки посторонних
                // совпадений, и проверка ложно считает поиск сломанным.
                query = full;
                probeWords = parts.map(function (w) { return w.toLowerCase(); });
            }
        } catch (eQ) { }
        var emptyFilters = null;
        try { emptyFilters = src.getFilterList(); } catch (e) { emptyFilters = null; }
        if (emptyFilters === null) { add("search", true, "не проверить"); next(); return; }
        var finishSearch = function (items, urlText) {
            var hits = 0;
            items.forEach(function (a) {
                var low = titleOf(a).toLowerCase();
                for (var w = 0; w < probeWords.length; w++) {
                    if (low.indexOf(probeWords[w]) >= 0) { hits++; break; }
                }
            });
            var ratio = items.length === 0 ? 0 : hits / items.length;
            var sample = items.slice(0, 3).map(titleOf);
            add("search", items.length > 0 && ratio >= 0.4,
                { total: items.length, relevant: hits, url: urlText, sample: sample });
        };
        var req = null;
        try { req = invoke(src, "searchAnimeRequest", [1, query, emptyFilters]); } catch (eReq) { req = null; }
        if (req !== null) {
            var resp = src.getClient().newCall(req).execute();
            finishSearch(animesOf(invoke(src, "searchAnimeParse", [resp])), String(req.url()).slice(0, 90));
            next();
            return;
        }
        // Источник на suspend-API: searchAnimeRequest — заглушка, идём через getSearchAnime.
        callAny(src, "getSearchAnime", [1, query, emptyFilters], 25000, function (page2) {
            finishSearch(animesOf(page2), "getSearchAnime");
            next();
        }, function (errS) { add("search", false, String(errS).slice(0, 80)); next(); });
    } catch (e) { add("search", false, String(e).slice(0, 80)); }
    next();
}

    function stepVideo(next) {
        try {
            if (spec.video === false) { add("video", true, "пропущено"); next(); return; }
            if (!st.ep) throw new Error("нет серии");
            // SEpisode — интерфейс, реальный класс SEpisodeImpl. Приводим к интерфейсу.
            var epObj = st.ep || st.epRaw;
            var epCast = iface(epObj, "eu.kanade.tachiyomi.animesource.model.SEpisode");
            if (epCast !== null) epObj = epCast;
            // Диагностика: у серии должен быть url, иначе расширение падает на null.
            var epUrl = "";
            try { epUrl = String(epObj.getUrl()); } catch (eU) { epUrl = ""; }
            try { Java.use("android.util.Log").e("StandVideo", "ep url=" + epUrl + " name=" + episodeNumber(epObj)); } catch (eV) { }
            var finish = function (vids) {
                if (vids === null || vids === undefined) {
                    add("video", false, "плеер вернул null вместо списка видео");
                    next();
                    return;
                }
                var list;
                var logV = function (m) {
                    try { Java.use("android.util.Log").e("StandVideo", String(m)); } catch (eL) { }
                };
                logV("finish: class=" + (function () { try { return String(vids.$className); } catch (eX) { return "?"; } })());
                try { list = Java.cast(vids, model("java.util.List")); }
                catch (e) { logV("cast error: " + e); list = vids; }
                var total = 0;
                try { total = list.size(); } catch (e) { total = -1; }
                var sample = [];
                for (var j = 0; j < total && j < 3; j++) {
                    var q = "", u = "";
                    try {
                        var v = Java.cast(list.get(j), model("eu.kanade.tachiyomi.animesource.model.Video"));
                        q = String(v.getQuality());
                        u = String(v.getVideoUrl());
                    } catch (e) { q = "?"; u = String(e); }
                    sample.push(q + "|" + u.substring(0, 45));
                }
                add("video", true, { count: total, sample: sample });
                next();
            };
            var fail = function (err) {
                // Если hoster-путь сорвался, пробуем legacy-вызов на том же объекте.
                try {
                    finish(callLegacy(src, "fetchVideoList", [epObj]));
                } catch (e2) { add("video", false, err + " | fetchVideoList: " + e2); next(); }
            };
            var hosterPath = function () {
            // Запасной путь: getHosterList(серия) → getVideoList(хосет) по каждому.
            // Legacy fetchVideoList в этой версии библиотеки не отражает то, что видит
            // пользователь: источники на hoster-API через него падают.
            callAny(src, "getHosterList", [epObj], 45000, function (hosters) {
                var list;
                try { list = Java.cast(hosters, model("java.util.List")); } catch (eCast) {
                    fail("hoster cast: " + eCast);
                    return;
                }
                var n = 0;
                try { n = list.size(); } catch (eSize) { n = 0; }
                if (n === 0) { add("video", false, "источник не вернул хостеров"); next(); return; }
                var sample = [], total = 0, seen = 0, take = Math.min(n, 2), info = [];
                var logV = function (m) { try { Java.use("android.util.Log").e("StandVideo", String(m)); } catch (eL) { } };
                var report = function () {
                    if (seen < take) return;
                    add("video", total > 0, { count: total, hosters: n, info: info, sample: sample.slice(0, 3) });
                    next();
                };
                for (var i = 0; i < take; i++) {
                    (function (idx) {
                        var hoster = null;
                        try { hoster = list.get(idx); } catch (eGet) { hoster = null; }
                        if (hoster === null || hoster === undefined) {
                            // Kotlin-список иногда отдаёт элементы только через toArray().
                            try { hoster = list.toArray()[idx]; } catch (eArr) { hoster = null; }
                        }
                        if (hoster === null || hoster === undefined) { seen++; report(); return; }
                        var hName = "?", hUrl = "?";
                        var hClass = "?";
                        try { hClass = String(hoster.getClass().getName()); } catch (eC0) { }
                        try { hoster = Java.cast(hoster, model("eu.kanade.tachiyomi.animesource.model.Hoster")); } catch (eC1) { logV("cast hoster: " + eC1); }
                        try { hName = String(hoster.getHosterName()); } catch (eN) {
                            logV("name err: " + eN);
                            try { hName = String(hoster.hosterName.value); } catch (eN2) { logV("name field err: " + eN2); }
                        }
                        try { hUrl = String(hoster.getHosterUrl()); } catch (eU) {
                            logV("url err: " + eU);
                            try { hUrl = String(hoster.hosterUrl.value); } catch (eU2) { logV("url field err: " + eU2); }
                        }
                        logV("hoster class: " + hClass);
                        info.push(hName + " | " + hUrl.substring(0, 80));
                        logV("hoster: " + hName + " | " + hUrl.substring(0, 80));
                        callAny(src, "getVideoList", [hoster], 45000, function (vids) {
                            seen++;
                            try {
                                var vs = Java.cast(vids, model("java.util.List"));
                                var c = vs.size();
                                total += c;
                                for (var j = 0; j < c && sample.length < 6; j++) {
                                    var q = "", u = "";
                                    try {
                                        var v = Java.cast(vs.get(j), model("eu.kanade.tachiyomi.animesource.model.Video"));
                                        q = String(v.getQuality());
                                        u = String(v.getVideoUrl());
                                    } catch (eOne) { q = "?"; u = String(eOne); }
                                    sample.push(q + "|" + u.substring(0, 45));
                                }
                            } catch (eCast2) { logV("video list: " + eCast2); }
                            report();
                        }, function (err) {
                            seen++;
                            logV("getVideoList: " + err);
                            report();
                        });
                    })(i);
                }
            }, fail);
            };
            // Основной путь приложения: getVideoList(серия) — библиотека сама
            // обходит хостеров и склеивает их видео.
            callAny(src, "getVideoList", [epObj], 60000, finish, function (errEp) {
                try { Java.use("android.util.Log").e("StandVideo", "episode path: " + errEp); } catch (eL) { }
                hosterPath();
            });
        } catch (e) { add("video", false, e); next(); }
    }

    if (spec.url) {
        // Режим одной страницы: каталог и прокрутка не нужны.
        st.first = animeFromUrl(spec.url);
        st.list = [st.first];
        stepDetails(function () {
            stepStatus(function () {
                stepThumb(function () {
                    stepThumbs(function () {
                        stepEpisodes(function () {
                            stepEpisodeUrls(function () {
                                stepFilters(function () {
                                    stepSearch(function () {
                                        stepVideo(done);
                                    });
                                });
                            });
                        });
                    });
                });
            });
        });
        return;
    }

    stepCatalog(function () {
        stepPages(function () {
            stepDup(function () {
                stepDetails(function () {
                    stepStatus(function () {
                        stepThumb(function () {
                            stepThumbs(function () {
                                stepEpisodes(function () {
                                    stepEpisodeUrls(function () {
                                        stepFilters(function () {
                                            stepSearch(function () {
                                                stepVideo(done);
                                            });
                                        });
                                    });
                                });
                            });
                        });
                    });
                });
            });
        });
    });
}

setTimeout(function () {
    Java.perform(function () {
        try { auditNext(); } catch (e) { send({ error: String(e) }); }
    });
}, 50);
send({ ready: true, count: SOURCES.length });
