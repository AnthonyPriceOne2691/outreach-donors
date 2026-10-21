/**
 * Наборы «Отправителя» по языку письма (`languages` у `GET/POST /sales/sender`): имя, должность,
 * подпись и физический адрес на каждом языке цепочек; пустое поле — общее. Своим файлом:
 * `salesTypes.ts` стоит у предела длины. Поля сверяет с сервером `tests/test_sales_kb_screen.py`.
 */

/** Поле набора на языке письма (`sender.LANGUAGE_FIELDS` сервера). */
export type SenderLanguageField =
  | 'sender_name'
  | 'sender_position'
  | 'signature'
  | 'physical_address';

/** Набор языка письма: пусто — в письмах на этом языке общее поле. */
export type SenderLanguageBody = Record<SenderLanguageField, string | null>;

/** Набор языка на экране: код и заголовок блока словами сервера («Письма на английском»). */
export interface SenderLanguageCard extends SenderLanguageBody {
  language: string;
  title: string;
}
