/**
 * Окно отчёта прогона — одно на «Сайты прогона» и «Расход и смета»: отчёт читается за один-два
 * экрана, а списки и расход открываются по кнопке (15.10.2026: отчёт большого прогона занимал
 * пять экранов на 1440 и девять на 390, почти половина — таблица «На проверку»).
 *
 * Окно Mantine с темой приложения: заголовок называет окно и прогон, фокус — внутри окна, Esc и
 * «Закрыть» закрывают его, фокус возвращается на ссылку или кнопку, которой его открыли; страница
 * под окном не прокручивается. **На телефоне — во весь экран**: с того же порога, где таблица
 * становится карточками (`phoneCards`), — у окна в 90 % экрана с полями по краям карточкам
 * оставалась щель. **Прокрутка одна** (`oneScroll`): шапка окна прозрачная и стоит, под ней
 * прокручивается середина — строки не проезжают под заголовком.
 */

import { Modal } from '@mantine/core';
import type { ModalProps } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import type { ReactNode } from 'react';

/** Порог телефона — тот же, что у карточек таблиц (`phoneCards` в `styles/glass.css`). */
export const PHONE = '(max-width: 47.99em)';

interface Props {
  opened: boolean;
  onClose: () => void;
  title: string;
  /** Ширина окна на широком экране. */
  size: NonNullable<ModalProps['size']>;
  /** Esc закрывает окно; поверх открыто второе окно — его Esc, а не этого. */
  closeOnEscape?: boolean;
  children: ReactNode;
}

export function ReportWindow({
  opened,
  onClose,
  title,
  size,
  closeOnEscape = true,
  children,
}: Props) {
  // С первой отрисовки: окно, открытое по ссылке, сразу встаёт своим размером.
  const phone = useMediaQuery(PHONE, false, { getInitialValueInEffect: false }) === true;
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={title}
      size={size}
      fullScreen={phone}
      closeOnEscape={closeOnEscape}
      classNames={{ content: 'oneScroll' }}
    >
      {/* Поля в 3 px держат кольцо фокуса внутри прокрутки (как у окна текста письма). */}
      <div className="oneScrollArea scrollSlim" style={{ padding: 3 }}>
        {children}
      </div>
    </Modal>
  );
}
