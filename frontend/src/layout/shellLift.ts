/**
 * Шапка уезжает со страницей, колонка меню поднимается вслед за ней.
 *
 * Замечание 25.09.2026: шапка стояла закреплённой, содержимое проезжало
 * под стеклом и было видно над ней и вокруг неё, а полезного в шапке нет —
 * имя сервиса, почта и выход. Anthony выбрал: шапка уезжает вместе со
 * страницей, как обычный заголовок, а меню остаётся на экране.
 *
 * Шапка для этого просто стоит в потоке страницы (правило в `glass.css`).
 * Колонка меню закреплена, и её верх — «высота шапки минус насколько шапка
 * уехала»: иначе над меню оставалась бы пустая полоса на месте шапки.
 * Смещение пишется переменной `--shell-lift` на корень рамы прямо в
 * обработчике прокрутки — один раз за кадр, без перехода: переход по `top`,
 * который Mantine вешает на колонку, отставал бы от страницы на 200 мс.
 */

/** Сколько пикселей шапки уже уехало вверх: от нуля до её высоты. */
export function liftFor(scrollY: number, headerHeight: number): number {
  return Math.min(Math.max(scrollY, 0), headerHeight);
}

/** Следит за прокруткой окна и пишет смещение на `root`. Возвращает отписку. */
export function followScroll(root: HTMLElement, headerHeight: number): () => void {
  const apply = () => {
    root.style.setProperty('--shell-lift', `${liftFor(window.scrollY, headerHeight)}px`);
  };
  apply();
  window.addEventListener('scroll', apply, { passive: true });
  return () => window.removeEventListener('scroll', apply);
}
