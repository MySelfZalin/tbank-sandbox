# Сертификаты клиента T-Invest

`tinvest-ca.pem` содержит Russian Trusted Root CA и Russian Trusted Sub CA.
Скачаны 10 октября 2026 с официального сервера Госуслуг:

- [Корневой сертификат](https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer)
- [Промежуточный сертификат](https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer)

[Установка на Госуслугах](https://www.gosuslugi.ru/crt) ·
[Инструкция T-Invest](https://developer.tbank.ru/invest/intro/developer/network)

SHA-256 сертификатов в DER:

| Сертификат | SHA-256 | Окончание срока UTC |
| --- | --- | --- |
| Root CA | D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31 | 2032-02-27 21:04:15 |
| Sub CA | BBBDE2103E790B999EC62BD03CF625A5A2E7C316E10AFE6A490EEDEAD8B3FD9B | 2027-03-06 11:25:19 |

Docker копирует файл в `/app/certs/tinvest-ca.pem` и задаёт `TINVEST_CA_FILE`.
Сертификаты загружает только HTTP-клиент T-Invest. Системные хранилища
не меняются; проверка TLS и имени сервера включена.

Для обновления скачайте актуальные сертификаты по официальным HTTPS-ссылкам,
проверьте сроки, отпечатки и подпись цепочки, замените bundle и пересоберите образ.
Сертификаты публичные: приватных ключей и токенов этот каталог не содержит.
