import {
  Badge,
  Button,
  Card,
  NavLink,
  Notification,
  SegmentedControl,
  Switch,
  Text,
  createTheme,
  Input,
  Modal,
  NumberInput,
  PasswordInput,
  Paper,
  Select,
  TagsInput,
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
    // Своя переменная, а не общая дымка: в тёмной теме поле — тёмный тон
    // (`--field-fill` в glass.css), иначе значение тонуло над ярким местом.
    background: 'var(--field-fill)',
    // Своя кромка, а не кромка стекла: на плотном стекле светлой темы
    // (окна) белая кромка не видна, и поле читалось строкой текста.
    borderColor: 'var(--field-edge)',
    backdropFilter: 'blur(14px)',
    WebkitBackdropFilter: 'blur(14px)',
    color: 'var(--ink)',
  },
} as const;

/** Список под полем и по его ширине. Без этого он уезжал влево и оказывался
 *  уже поля — видно на снимке. Один на все поля со списком. */
const dropdownBelow = {
  position: 'bottom-start',
  width: 'target',
  offset: 6,
  transitionProps: { transition: 'pop', duration: 180 },
} as const;

/** Шрифтовой ряд сервиса. Вынесен из темы, потому что по нему же холст
 *  меряет подписи меню (`layout/navWidth.ts`): второй экземпляр строки
 *  разошёлся бы с первым на первой правке. */
export const FONT_STACK =
  'ui-sans-serif, -apple-system, "SF Pro Text", Inter, "Segoe UI", system-ui, sans-serif';

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
  fontFamily: FONT_STACK,
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
    // Цвет `Text` у Mantine — `color: var(--text-color)` без запасного
    // значения, и переменную компонент ставит себе только при пропсе
    // `color`. Без пропса она не определена, и цвет наследуется — но только
    // пока её не определил кто-то выше: переменная сама наследуется. Имя
    // ходовое, расширения браузера ставят его на всю страницу, и 25.09.2026
    // у Anthony в тёмной теме почернели «Вошли как», номера прогонов и
    // вердикты судьи — ровно `#000` по пикселям снимка, не наши чернила.
    //
    // `currentColor` в свойстве `color` означает «как у родителя» — то же
    // наследование, только объявленное самим компонентом, и чужое значение
    // до него не доходит. Цвет из пропса Mantine кладёт позже темы, щит его
    // не перекрывает; `c` пишется свойством `color` напрямую и не зависит
    // от переменной вовсе.
    Text: Text.extend({
      styles: (_theme, props) => ({
        root: props.color ? {} : { '--text-color': 'currentColor' },
      }),
    }),
    // Стекло поля задаётся один раз — у `Input`, на котором построены все
    // поля Mantine: стили темы для `Input` получают и `TextInput`, и `Select`,
    // и `TagsInput`. До 25.09.2026 оно перечислялось у каждого поля по имени,
    // и `TagsInput` («Про что» на экране прогона), которого в списке не было,
    // стоял белым непрозрачным полем посреди стекла (аудит, №26). Новое поле
    // теперь стеклянное само, а не после того, как его заметят на снимке.
    Input: Input.extend({ styles: glassField }),
    TextInput: TextInput.extend({ defaultProps: { radius: 'xl' } }),
    PasswordInput: PasswordInput.extend({ defaultProps: { radius: 'xl' } }),
    NumberInput: NumberInput.extend({ defaultProps: { radius: 'xl' } }),
    // Многострочное поле — то же стекло: белая простыня посреди
    // полупрозрачной панели видна первой, а это всего лишь поле ввода.
    Textarea: Textarea.extend({ defaultProps: { radius: 'lg' } }),
    Select: Select.extend({ defaultProps: { radius: 'xl', comboboxProps: dropdownBelow } }),
    TagsInput: TagsInput.extend({ defaultProps: { radius: 'xl', comboboxProps: dropdownBelow } }),
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
      //
      // Стекло — у окна, а не у его шапки. Шапка с тем же классом стояла
      // отдельной плитой внутри окна — со своей кромкой, тенью и радиусом
      // 28, а заголовок оказывался в шести пикселях от её края (аудит
      // 25.09.2026, №24). Фон шапки прозрачный: умолчание Mantine — цвет
      // страницы, в тёмной теме это непрозрачная серая полоса поверх стекла.
      classNames: { content: 'glassSolid' },
      styles: { header: { background: 'transparent' } },
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
