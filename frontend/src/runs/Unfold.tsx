/**
 * Секция, которая раскрывается в два такта: сначала раздвигается место,
 * потом проявляется содержимое. Закрывается наоборот: содержимое гаснет,
 * потом секция сдвигается.
 *
 * Замечание 25.09.2026 про переключатель «Свои ключи» ↔ «Собрать моделью»:
 * «при переключении сама секция сначала плавно раздвигается, и потом
 * появляется содержимое». До этого блок сборки появлялся мгновенно, и поле
 * ключей под ним прыгало вниз на полторы сотни пикселей: глаз терял, что
 * изменилось.
 *
 * **Раздвигается сама рамка, а не окно поверх неё.** Высота, поля, кромка
 * и отступ рамки растут от нуля до своих: скруглённые углы и тень остаются
 * при ней весь ход. Обёртка с `overflow: hidden` резала бы тень и нижние
 * углы, и они появлялись бы рывком в последнем кадре.
 *
 * **Закрытое не отрисовывается.** Когда секция сдвинулась, её содержимого
 * нет в документе — не спрятано, а нет: тест не найдёт скрытого поля,
 * клавиатура в него не зайдёт (урок 21.09.2026 про `hidden`).
 *
 * **Проверяется по кадрам, а не глазом.** Замер (`requestAnimationFrame`)
 * снимает высоту рамки и прозрачность содержимого: высота растёт монотонно,
 * прозрачность трогается только после того, как высота дошла. Метки
 * `data-unfold` — для этого замера.
 *
 * Движение выключается при `prefers-reduced-motion` и там, где его нет
 * вовсе (jsdom): секция появляется и исчезает сразу.
 */

import { Box } from '@mantine/core';
import type { BoxProps } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

/** Кривая «Движения» из правил интерфейса. */
const EASE = 'cubic-bezier(0.32, 0.72, 0, 1)';

/** Такты, мс. Каждый — в пределах «Движения» (160–260 мс); вместе открытие
 *  длится ~380 мс: это два перехода подряд, а не один затянутый. Кривая
 *  тормозит к концу, и высота проходит 97% пути за половину такта —
 *  поэтому такт высоты короче, чем мог бы: длиннее — и между «раздвинулось»
 *  и «проявилось» глазу видна пустая рамка (замер по кадрам 25.09.2026:
 *  при 240 мс рамка стояла пустой ~200 мс). */
export const UNFOLD_MS = { grow: 200, show: 180, hide: 160, shrink: 200 } as const;

/** Что растёт вместе с высотой: без полей, кромки и отступа рамка в ноль
 *  не сходится — от неё оставалась бы полоска в три десятка пикселей. */
interface Frame {
  [side: string]: string;
  height: string;
  paddingTop: string;
  paddingBottom: string;
  borderTopWidth: string;
  borderBottomWidth: string;
  marginTop: string;
}

const FOLDED: Frame = {
  height: '0px',
  paddingTop: '0px',
  paddingBottom: '0px',
  borderTopWidth: '0px',
  borderBottomWidth: '0px',
  marginTop: '0px',
};

/** Рамка как она есть в этот кадр — с учётом идущего хода. */
function frameOf(element: HTMLElement): Frame {
  const style = getComputedStyle(element);
  return {
    height: `${element.getBoundingClientRect().height}px`,
    paddingTop: style.paddingTop,
    paddingBottom: style.paddingBottom,
    borderTopWidth: style.borderTopWidth,
    borderBottomWidth: style.borderBottomWidth,
    marginTop: style.marginTop,
  };
}

function stop(element: HTMLElement): void {
  for (const running of element.getAnimations()) running.cancel();
}

/** Доля оставшегося пути: прерванный ход доигрывается за свою долю
 *  времени, а не начинается заново медленным. */
function share(done: number, whole: number): number {
  return whole <= 0 ? 1 : Math.min(1, Math.max(0.25, 1 - done / whole));
}

/** Ход закончился — или его отменил следующий. Второе не ошибка. */
async function played(animation: Animation): Promise<boolean> {
  try {
    await animation.finished;
    return true;
  } catch {
    return false;
  }
}

interface Props extends BoxProps {
  open: boolean;
  children: ReactNode;
}

export function Unfold({ open, children, ...frameProps }: Props) {
  const reduced = useReducedMotion();
  // Отрисована ли секция. Открытие отрисовывает сразу, закрытие — после
  // того, как секция сдвинулась.
  const [shown, setShown] = useState(open);
  if (open && !shown) setShown(true);

  const frame = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  // Номер хода: следующее нажатие отменяет предыдущий ход на полпути.
  const turn = useRef(0);
  // Секция с первого кадра открыта — её не раздвигают: это часть экрана.
  const first = useRef(true);

  useLayoutEffect(() => {
    const box = frame.current;
    const content = body.current;
    const mounted = first.current;
    first.current = false;
    if (!shown || box === null || content === null) return;

    const mine = ++turn.current;
    const current = () => turn.current === mine;
    const moving = !reduced && !mounted && typeof box.animate === 'function';

    if (!moving) {
      if (typeof box.getAnimations === 'function') {
        stop(box);
        stop(content);
      }
      box.style.overflow = '';
      content.style.opacity = '';
      if (!open) setShown(false);
      return;
    }

    // Откуда идём — снимается до отмены прежнего хода: пока он идёт,
    // вычисленный стиль и есть его текущий кадр. Только что отрисованная
    // секция идёт с нуля.
    const fresh = box.getAnimations().length === 0 && content.getAnimations().length === 0;
    const from = open && fresh ? FOLDED : frameOf(box);
    const seen = open && fresh ? 0 : Number(getComputedStyle(content).opacity);
    stop(box);
    stop(content);
    // Куда: рамка без хода — своего размера.
    const natural = frameOf(box);
    const whole = parseFloat(natural.height);
    const done = parseFloat(from.height);
    content.style.opacity = String(seen);

    const unfold = async () => {
      if (done < whole - 0.5) {
        box.style.overflow = 'hidden';
        const grow = box.animate([from, natural], {
          duration: UNFOLD_MS.grow * share(done, whole),
          easing: EASE,
        });
        if (!(await played(grow)) || !current()) return;
      }
      box.style.overflow = '';
      const show = content.animate([{ opacity: seen }, { opacity: 1 }], {
        duration: UNFOLD_MS.show * share(seen, 1),
        easing: EASE,
        fill: 'forwards',
      });
      if (!(await played(show)) || !current()) return;
      content.style.opacity = '';
      show.cancel();
    };

    const fold = async () => {
      if (seen > 0.01) {
        const hide = content.animate([{ opacity: seen }, { opacity: 0 }], {
          duration: UNFOLD_MS.hide * seen,
          easing: EASE,
          fill: 'forwards',
        });
        if (!(await played(hide)) || !current()) return;
      }
      content.style.opacity = '0';
      stop(content);
      box.style.overflow = 'hidden';
      const shrink = box.animate([from, FOLDED], {
        duration: UNFOLD_MS.shrink * share(whole - done, whole),
        easing: EASE,
        fill: 'forwards',
      });
      if (!(await played(shrink)) || !current()) return;
      setShown(false);
    };

    void (open ? unfold() : fold());
  }, [open, shown, reduced]);

  if (!shown) return null;
  return (
    <Box ref={frame} data-unfold="frame" {...frameProps}>
      <div ref={body} data-unfold="content">
        {children}
      </div>
    </Box>
  );
}
