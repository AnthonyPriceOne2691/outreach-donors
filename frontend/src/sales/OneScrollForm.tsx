/**
 * Форма окна раздела — с одной прокруткой (правило «В окне одна прокрутка», `docs/UI_RULES.md`;
 * замечание Anthony 12.10.2026 к окну письма: «двойная прокрутка… прям косяк»).
 *
 * Окно Mantine не выше 90 % экрана и прокручивается само, когда содержимое выше, а поле
 * с потолком строк (`maxRows`) прокручивается внутри: прокруток в окне становилось две,
 * а кнопки уезжали под край экрана. Замер стенда 13.10.2026 (длинный текст в поле):
 * запись базы знаний — на всех пяти экранах, от 1440 × 900 до 375 × 667; шаг цепочки
 * и стоп-лист — от 1280 × 720 и меньше; гипотеза — на 844 × 390; лид — на 375 × 667
 * и 844 × 390. Теперь окно — столбиком (`oneScroll` в `glass.css`), и форма — тем же
 * столбиком: заголовок и кнопки стоят, прокручивается середина, поле в ней растёт по тексту.
 *
 * **Отказ сервера — над прокруткой** (правило 10.10.2026: отказ по форме — над полями): он
 * встаёт после нажатия кнопки внизу, и в начале прокрученной середины его бы не увидели.
 * **Под прокруткой — кнопки и строка «почему закрыто»** (`WriteRight`): причина выключенной
 * кнопки — строкой под ней.
 *
 * Окну нужен класс `oneScroll` (`classNames={{ content: 'oneScroll' }}`): без него форма —
 * столбик внутри окна, которое прокручивается само.
 */

import { Box, getSpacing, Stack } from '@mantine/core';
import type { MantineSpacing } from '@mantine/core';
import { useEffect, useRef } from 'react';
import type { CSSProperties, FormEvent, ReactNode } from 'react';

interface Props {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  /** Над прокруткой — отказ сервера; нет отказа — ничего, и пустого места не остаётся. */
  top?: ReactNode;
  /** Середина — поля; прокручивается она одна. */
  children: ReactNode;
  /** Под прокруткой — кнопки и строка «почему закрыто». */
  footer: ReactNode;
  /** Шаг между полями — он же между отказом, серединой и кнопками. */
  gap?: MantineSpacing;
}

export function OneScrollForm({ onSubmit, top, children, footer, gap = 'sm' }: Props) {
  // Форма — тем же столбиком, что тело окна (`oneScroll`): середина забирает остаток рамки
  // и ужимается меньше своего содержимого только при `min-height: 0` у всей цепочки.
  const column: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    flex: '1 1 auto',
    minHeight: 0,
    gap: getSpacing(gap),
  };
  return (
    <form onSubmit={onSubmit} style={column}>
      {/* Поле в 3 px от края держит кольцо фокуса внутри прокрутки — на те же 3 px
          отступает и отказ над ней, кромкой вровень с полями. */}
      {top ? <Box px={3}>{top}</Box> : null}
      <div className="oneScrollArea scrollSlim">
        <Stack gap={gap} p={3}>
          {children}
        </Stack>
      </div>
      {footer}
    </form>
  );
}

/**
 * Показать в середине то, что встало в ней после нажатия кнопки внизу: письмо глазами
 * адресата, непринятые строки стоп-листа. Кнопка стоит, а встаёт оно в прокрутке — часто
 * за краем видимого, и нажатие выглядело бы как «ничего не случилось». `shown` — что
 * показано; пусто (`null`, `undefined`) — не двигать.
 *
 * Двигается одна середина, верхом показанного к её верху, — сдвиг считается прямо.
 * `scrollIntoView` по стандарту двигает все прокрутки над показанным, вплоть до страницы под
 * окном; Chromium стенда страницу не сдвинул (замер 13.10.2026), но обещать это за все
 * браузеры нечем.
 */
export function useReveal(shown: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const target = ref.current;
    const middle = target?.closest<HTMLElement>('.oneScrollArea');
    if (shown === null || shown === undefined || !target || !middle) return;
    middle.scrollTop += target.getBoundingClientRect().top - middle.getBoundingClientRect().top;
  }, [shown]);
  return ref;
}
