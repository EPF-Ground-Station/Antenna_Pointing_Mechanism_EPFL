#ifndef ANTENNA_H
#define ANTENNA_H

#include <SPI.h>

#include "define.h"

#include "Stepper.h"
#include "Encoder.h"
#include "EncoderMultiTurn.h"

class Antenna {

    private:

    Stepper *az_stepper = nullptr;
    EncoderMultiTurn *az_encoder= nullptr;

    Stepper *elev_stepper = nullptr;
    Encoder *elev_encoder = nullptr;

    int az_init_turn_count;

    public:

    Antenna(){

        az_stepper = new Stepper(
            AZ_STEPPER_STEP_PIN,
            AZ_STEPPER_DIR_PIN,
            AZ_STEPPER_ENABLE_PIN,
            AZ_STEPPER_BOOST_PIN,
            AZ_STEPPER_FAULT_PIN,
            AZ_STEP_PERIOD_uS);

        elev_stepper = new Stepper(
            ELEV_STEPPER_STEP_PIN,
            ELEV_STEPPER_DIR_PIN,
            ELEV_STEPPER_ENABLE_PIN,
            ELEV_STEPPER_BOOST_PIN,
            ELEV_STEPPER_FAULT_PIN,
            ELEV_STEP_PERIOD_uS);

        ENCODERS_SPI.begin();
        ENCODERS_SPI.beginTransaction(SPISettings(SPI_SPEED, MSBFIRST, SPI_MODE1));

        az_encoder = new EncoderMultiTurn(
            ENCODERS_SPI, 
            AZ_ENCODER_NCS_PIN);

        elev_encoder = new Encoder(
            ENCODERS_SPI,
            ELEV_ENCODER_NCS_PIN);


        // read some values to clean the SPI bus    
        for(int i = 0; i < 10; i++){
            az_encoder->get_encoder_pos_value();
            delay(50);
            elev_encoder->get_encoder_pos_value();
            delay(50);
        }
        // safety check assume the antenna won't do a full turn on az when disconnected
        // also assume : 0 << init turn count << Max Turn Count, so the encoder turn counter won't under/overflow
        az_init_turn_count = az_encoder->get_turn_count();

    }

    ~Antenna(){

        delete(az_stepper);
        delete(elev_stepper);

        delete(az_encoder);
        delete(elev_encoder);
    }

    void go_home(){

        int az_current_turn_count = az_encoder->get_turn_count();

        // untangle the cables
        az_stepper->step((az_current_turn_count - az_init_turn_count)*AZ_MICRO_STEP_PER_TURN*AZ_REDUC);

        point_to(0, 90.0 - ELEV_ZENITH_SAFETY_MARGIN_DEG);

    }

    void empty_water(){

        point_to(0, 60);

        delay(3000);

        go_home();
        
    }

    // az and elev are in degree
    // az grow to the east (aimed at the north)
    // elev is 0° at the horizon and grow toward zenith
    void point_to(float az_deg, float elev_deg){

        // ------ point az ------------

        // not sure how % behave with value < 0 so convert first
        while (az_deg < 0.0){ az_deg += 360.0;}

        int az_target_encoder_val = (int)(az_deg / 360.0 * ENCODERS_MAX + AZ_NORTH_ENCODER_VAL) % ENCODERS_MAX;

        int az_current_encoder_val = az_encoder->get_encoder_pos_value();

        int az_encoder_val_diff = az_target_encoder_val - az_current_encoder_val;

        // take the shortest path

        if( abs(az_encoder_val_diff) > ENCODERS_MAX/2){

            if(az_encoder_val_diff < 0){
                az_encoder_val_diff = az_encoder_val_diff + ENCODERS_MAX;

            } else{
                az_encoder_val_diff = az_encoder_val_diff - ENCODERS_MAX;
            }
            
        }

        // predicting if next move will be too much and untangle cables first if needed

        int az_current_turn_count = az_encoder->get_turn_count();

        float az_pred_diff_deg_since_init = ((float)(az_current_turn_count - az_init_turn_count) + (az_current_encoder_val + az_encoder_val_diff)/ENCODERS_MAX ) * 360.0;

        if(az_pred_diff_deg_since_init > AZ_MAX_ROTATION_DEG){
            az_stepper->step(-AZ_MICRO_STEP_PER_TURN*AZ_REDUC);
        }
        if(az_pred_diff_deg_since_init < -AZ_MAX_ROTATION_DEG){
            az_stepper->step(AZ_MICRO_STEP_PER_TURN*AZ_REDUC);
        }

        int az_step_to_turn = (float)az_encoder_val_diff / ENCODERS_MAX * AZ_REDUC * AZ_MICRO_STEP_PER_TURN;

        az_stepper->step(az_step_to_turn);

        //------ point elev --------------

        // safety check

        if(elev_deg > 90.0 - ELEV_ZENITH_SAFETY_MARGIN_DEG){
            elev_deg = 90.0 - ELEV_ZENITH_SAFETY_MARGIN_DEG;
        }
        if(elev_deg < 0.0){
            elev_deg = 0.0;
        }

        // ENCODERS_MAX is added in case ELEV_ZENITH_ENCODER_VAL is less than ENCODERS_MAX/4 and the result is negative
        // when it's not the case "% ENCODERS_MAX" remove the excess turn
        const int ELEV_HORIZON_ENCODER_OFFSET_VAL = (int)(ELEV_ZENITH_ENCODER_VAL - ENCODERS_MAX/4 + ENCODERS_MAX) % ENCODERS_MAX;

        int elev_target_encoder_val = (int)(elev_deg / 360.0 * ENCODERS_MAX + ELEV_HORIZON_ENCODER_OFFSET_VAL) % ENCODERS_MAX;

        int elev_current_encoder_val = elev_encoder->get_encoder_pos_value();

        int elev_encoder_val_diff = elev_target_encoder_val - elev_current_encoder_val;

        // take the shortest path

        if( abs(elev_encoder_val_diff) > ENCODERS_MAX/2){

            if(elev_encoder_val_diff < 0){
                elev_encoder_val_diff = elev_encoder_val_diff + ENCODERS_MAX;

            } else{
                elev_encoder_val_diff = elev_encoder_val_diff - ENCODERS_MAX;
            }
            
        }

        int elev_step_to_turn = (float)elev_encoder_val_diff / ENCODERS_MAX * ELEV_REDUC * ELEV_MICRO_STEP_PER_TURN;

        elev_stepper->step(-elev_step_to_turn);

    }

};

#endif