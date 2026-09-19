import {
  Button,
  Card,
  NavLink,
  createTheme,
  Modal,
  Notification,
  NumberInput,
  PasswordInput,
  Paper,
  Select,
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
  primaryShade: { light: 6, dark: 5 },
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
    Card: Card.extend({ defaultProps: { radius: 'xl', withBorder: false } }),
    Paper: Paper.extend({ defaultProps: { radius: 'xl' } }),
    TextInput: TextInput.extend({ defaultProps: { radius: 'xl' }, styles: glassField }),
    PasswordInput: PasswordInput.extend({ defaultProps: { radius: 'xl' }, styles: glassField }),
    NumberInput: NumberInput.extend({ defaultProps: { radius: 'xl' }, styles: glassField }),
    Select: Select.extend({
      defaultProps: { radius: 'xl' },
      styles: { ...glassField, dropdown: { backdropFilter: 'blur(20px)' } },
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
      classNames: { root: 'glassPanel' },
      styles: {
        title: { color: 'var(--ink)' },
        description: { color: 'var(--ink-soft)' },
      },
    }),
    // Подсвеченный пункт меню Mantine красит бледным оттенком акцента:
    // поверх собственной подложки пункта это замерено 3.96 : 1 при норме
    // 4.5. Подсветка остаётся подложкой, а буквы — обычными чернилами.
    NavLink: NavLink.extend({ styles: { label: { color: 'var(--ink)' } } }),
    Modal: Modal.extend({
      classNames: { content: 'glassPanel', header: 'glassPanel' },
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
