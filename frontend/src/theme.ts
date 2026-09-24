import {
  Badge,
  Button,
  Card,
  NavLink,
  Notification,
  SegmentedControl,
  Switch,
  createTheme,
  Modal,
  NumberInput,
  PasswordInput,
  Paper,
  Select,
  Textarea,
  TextInput,
  rem,
} from '@mantine/core';

/**
 * Тема Mantine поверх стеклянных токенов (`styles/glass.css`).
 *
 * Здесь только то, что Mantine рисует сам и без чего компоненты
 * выбиваются из языка: скругления, палитра акцента, шрифтовой ряд.
 * Сами поверхности — в CSS, потому что их переиспользуют и обычные
 * элементы, у которых нет компонента Mantine.
 *
 * **Скругления крупнее умолчания намеренно.** Кнопка-пилюля и панель
 * с радиусом в 1.75rem — это то самое «обтекаемое», ради чего выбран
 * язык: угол в 4px рядом с размытым стеклом читается как чужая деталь.
 */

/** Бирюза лагуны: акцент сервиса. Десять ступеней, шестая — рабочая. */
const lagoon: [string, string, string, string, string, string, string, string, string, string] = [
  'oklch(97% 0.02 195)',
  'oklch(94% 0.04 195)',
  'oklch(89% 0.06 195)',
  'oklch(83% 0.09 195)',
  'oklch(77% 0.11 195)',
  'oklch(71% 0.125 195)',
  'oklch(64% 0.13 195)',
  'oklch(56% 0.13 195)',
  'oklch(48% 0.11 195)',
  'oklch(39% 0.09 195)',
];

/** Поле ввода на стекле. Белое поле поверх полупрозрачной панели читается
 *  как заплатка из другого макета: оно единственное на экране непрозрачное. */
const glassField = {
  input: {
    background: 'var(--glass-fill-quiet)',
    borderColor: 'var(--glass-edge)',
    backdropFilter: 'blur(14px)',
    WebkitBackdropFilter: 'blur(14px)',
    color: 'var(--ink)',
  },
} as const;

export const theme = createTheme({
  primaryColor: 'lagoon',
  /* Ступень заливки глубже рабочей: на рабочей белый текст даёт 2,7 : 1
     при норме 4,5 — замерено по пикселям снимка в обеих темах. Дефект
     был не одного экрана, а главной кнопки всего сервиса; не видели его
     потому, что там, где меряли, кнопка стояла выключенной, а у неё
     меряется серое. */
  primaryShade: { light: 8, dark: 8 },
  /* Заливной градиент — тоже один раз и тоже глубокими ступенями.
     Ступени, повторённые у каждой кнопки, правку глубины не получают:
     после починки главной кнопки семь из восьми остались на рабочей,
     и «Запустить» намерилась на 2,87 : 1 при норме 3. */
  defaultGradient: { from: 'lagoon.7', to: 'lagoon.9', deg: 135 },
  colors: { lagoon },
  defaultRadius: 'lg',
  fontFamily:
    'ui-sans-serif, -apple-system, "SF Pro Text", Inter, "Segoe UI", system-ui, sans-serif',
  headings: { fontWeight: '650' },
  radius: {
    xs: rem(8),
    sm: rem(12),
    md: rem(16),
    lg: rem(20),
    xl: rem(28),
  },
  components: {
    // Кнопки — пилюли. Радиус задаётся умолчанием компонента, а не
    // прописывается на каждом вызове: пропущенный проп иначе всплывает
    // одной квадратной кнопкой на третьем экране.
    Button: Button.extend({ defaultProps: { radius: 'xl' } }),
    // Umlaut Mantine поднимает подпись в верхний регистр. По-английски это
    // читается как ярлык, по-русски — как ошибка вёрстки: «НЕ СМЕНИЛ
    // РАЗОВЫЙ ПАРОЛЬ» кричит громче самого заголовка экрана.
    Badge: Badge.extend({ defaultProps: { radius: 'xl', tt: 'none' } }),
    Card: Card.extend({ defaultProps: { radius: 'xl', withBorder: false } }),
    Paper: Paper.extend({ defaultProps: { radius: 'xl' } }),
    TextInput: TextInput.extend({ defaultProps: { radius: 'xl' }, styles: glassField }),
    PasswordInput: PasswordInput.extend({ defaultProps: { radius: 'xl' }, styles: glassField }),
    NumberInput: NumberInput.extend({ defaultProps: { radius: 'xl' }, styles: glassField }),
    // Многострочное поле — то же стекло: белая простыня посреди
    // полупрозрачной панели видна первой, а это всего лишь поле ввода.
    Textarea: Textarea.extend({ defaultProps: { radius: 'lg' }, styles: glassField }),
    Select: Select.extend({
      defaultProps: {
        radius: 'xl',
        // Список раскрывается под полем и по его ширине. Без этого он
        // уезжал влево и оказывался уже поля — видно на снимке.
        comboboxProps: {
          position: 'bottom-start',
          width: 'target',
          offset: 6,
          transitionProps: { transition: 'pop', duration: 180 },
        },
      },
      styles: glassField,
    }),
    // Уведомление всплывает поверх работы — ему тем более нельзя быть
    // единственной непрозрачной плашкой на экране.
    //
    // Цвет пояснения задаётся явно: Mantine пишет его серым шестой
    // ступени, вшитым в стиль, а не переменной, и поверх стекла это
    // замерено 2.3 : 1 на свету и 1.9 : 1 в темноте при норме 4.5.
    // Именно этим текстом уведомление говорит, что делать, — заголовок
    // без него сообщает только факт отказа.
    Notification: Notification.extend({
      defaultProps: { radius: 'lg' },
      // Заливка рамы, а не обычная: уведомление всплывает над любым местом
      // полотна, и сквозь тонкое стекло просвечивает то пятно, над которым
      // оно оказалось. Сообщение об успехе поверх кораллового пятна
      // выглядело отказом — цвет должен идти от смысла, а не от фона.
      classNames: { root: 'glassFrame' },
      styles: {
        title: { color: 'var(--ink)' },
        description: { color: 'var(--ink-soft)' },
      },
    }),
    // Подсвеченный пункт меню Mantine красит бледным оттенком акцента:
    // поверх собственной подложки пункта это замерено 3.96 : 1 при норме
    // 4.5. Подсветка остаётся подложкой, а буквы — обычными чернилами.
    NavLink: NavLink.extend({ styles: { label: { color: 'var(--ink)' } } }),
    // Переключатель тем брал серый Mantine и не совпадал с палитрой.
    // Подвижная подложка красится акцентом и едет с той же кривой, что
    // и остальные переходы сервиса.
    SegmentedControl: SegmentedControl.extend({
      defaultProps: {
        radius: 'xl',
        transitionDuration: 220,
        transitionTimingFunction: 'cubic-bezier(0.32, 0.72, 0, 1)',
        // Линейки между сегментами берут цвет рамок Mantine и на стекле
        // читаются как случайная красная черта — видно на снимке.
        withItemsBorders: false,
      },
      styles: {
        root: { background: 'var(--segment-fill)', border: '1px solid var(--glass-edge)' },
        indicator: {
          background: 'color-mix(in oklab, var(--accent) 26%, transparent)',
          border: '1px solid var(--glass-edge)',
          boxShadow: 'none',
        },
        // Значок в сегменте — строчный элемент, и без выравнивания он
        // садится на базовую линию текста, то есть выше середины.
        //
        // Цвета здесь нет намеренно: он зависит от того, выбран сегмент
        // или нет, а `styles` едет инлайном и перебивает любое правило
        // с `[data-active]`. Обе ступени — в `glass.css`.
        label: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        },
      },
    }),
    Switch: Switch.extend({ defaultProps: { radius: 'xl' } }),
    Modal: Modal.extend({
      // Окно встаёт поверх работы: сквозь него не должно быть видно
      // таблицу под ним, иначе подписи накладываются друг на друга.
      classNames: { content: 'glassSolid', header: 'glassSolid' },
      defaultProps: {
        radius: 'xl',
        centered: true,
        // Подложка сама размывает фон: без этого стеклянное окно стоит
        // поверх резкой страницы и выглядит вырезанным из другого макета.
        overlayProps: { backgroundOpacity: 0.35, blur: 8 },
        transitionProps: { transition: 'pop', duration: 220 },
      },
    }),
  },
});
